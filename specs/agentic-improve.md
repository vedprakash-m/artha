# Artha — Agentic Intelligence Improvement Plan

> **Version**: 1.0 | **Status**: Proposed | **Date**: March 15, 2026
> **Author**: Lead Principal System Architect | **Classification**: Internal — Architecture
> **Triggered by**: Framework Deconstruction Analysis (AutoGen, Semantic Kernel, LangChain/LangGraph, Pydantic AI, OpenAI Agents SDK, Agno)
> **Related**: [specs/deep-agents.md](deep-agents.md) (Phases 1–5, implemented), [specs/vm-hardening.md](vm-hardening.md) (Phases 0–5, implemented)
> **Supersedes**: deep-agents.md Phases 6–7 (State Abstraction [deferred], Sub-Task Isolation [experimental])
> **Methodology**: Strangler Fig incremental migration, DDD bounded contexts, SOLID/SRP

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [AS-IS Analysis — Foundation Audit](#2-as-is-analysis--foundation-audit)
3. [Pain Points — Next-Generation Gaps](#3-pain-points--next-generation-gaps)
4. [Framework Primitives — Extraction & Mapping](#4-framework-primitives--extraction--mapping)
5. [Design Proposals & Trade-Off Matrices](#5-design-proposals--trade-off-matrices)
6. [Execution Plan — Strangler Fig Migration](#6-execution-plan--strangler-fig-migration)
   - [Phase 1: OODA Analysis Loop](#phase-1-ooda-analysis-loop-prompt-only)
   - [Phase 2: Tiered Eviction Priority](#phase-2-tiered-eviction-priority)
   - [Phase 3: ArthaContext Typed Runtime Context](#phase-3-arthacontext-typed-runtime-context)
   - [Phase 4: Implicit Checkpoints & Step Resumption](#phase-4-implicit-checkpoints--step-resumption)
   - [Phase 5: Persistent Fact Extraction](#phase-5-persistent-fact-extraction)
   - [Phase 6: Sub-Agent Context Isolation with Handoff Compression](#phase-6-sub-agent-context-isolation-with-handoff-compression)
7. [Risk Register](#7-risk-register)
8. [Trade-Off Decision Log](#8-trade-off-decision-log)
9. [Assumptions & Constraints](#9-assumptions--constraints)
10. [Success Criteria](#10-success-criteria)
11. [Dependency Map & Critical Path](#11-dependency-map--critical-path)
12. [Backward Compatibility Contract](#12-backward-compatibility-contract)
13. [Observability Strategy](#13-observability-strategy)
14. [Security Analysis](#14-security-analysis)

---

## 1. Executive Summary

### Context

The Deep Agents Core Harness (Phases 1–5, TS v3.6) and VM Operational
Hardening (Phases 0–5, TS v3.7) have been fully implemented, delivering:

| Delivered Capability | Lines | Phase |
|----------------------|:-----:|:-----:|
| Context offloading (`context_offloader.py`) | ~250 | DA-1 |
| Progressive domain disclosure (`domain_index.py`) | ~200 | DA-2 |
| Session summarization (`session_summarizer.py`) | ~300 | DA-3 |
| Middleware stack (5 modules) | ~500 | DA-4 |
| Structured output schemas (3 modules) | ~330 | DA-5 |
| Environment detection (`detect_environment.py`) | ~200 | VM-1 |
| Preflight hardening (advisory, profile, tokens) | ~200Δ | VM-2 |
| Instruction decomposition (compact mode, §R router) | ~400Δ | VM-3 |
| Workflow compliance gates (⛩️ gates, Silent Failure Block) | ~500Δ | VM-4 |
| Post-catch-up compliance audit (`audit_compliance.py`) | ~250 | VM-5 |

**Total delivered: ~3,100+ lines of production code across 15 modules.**

### What This Plan Addresses

With the infrastructure foundation solid, this plan targets the **next
tier of agentic intelligence** — making Artha smarter, more self-aware,
and capable of learning across sessions. Six AI agent frameworks were
deconstructed to extract architectural primitives without importing
framework baggage:

| Framework | Primitive Extracted | Artha Application |
|-----------|--------------------|--------------------|
| **Agno** | Tiered memory eviction | Priority-ordered context offloading |
| **Semantic Kernel + LangGraph** | OODA decision loop | Structured analysis protocol for Step 8 |
| **Pydantic AI** | `RunContextWrapper` typed dependency injection | `ArthaContext` runtime carrier object |
| **LangGraph** | Implicit state checkpoints | Step resumption from `tmp/` artifacts |
| **OpenAI Agents SDK** | Parallel input/output guardrails | Concurrent PII + write guard pre-checks |
| **Agno** | Persistent extracted facts across sessions | `state/memory.md` fact schema |
| **OpenAI Agents SDK** | Handoff input filter (context compression) | Sub-agent delegation with history trimming |
| **AutoGen** | RoutedAgent pub/sub (agent ↔ runtime decoupling) | Inform sub-agent isolation design |

### What This Is NOT

- **Not a framework adoption.** Zero external framework dependencies.
  Each primitive is extracted, adapted, and implemented as native Artha code.
- **Not a rewrite.** All changes are additive Strangler Fig layers on top
  of the working DA-1–5 and VM-0–5 infrastructure.
- **Not speculative.** Every phase addresses a measured pain point with
  a specific, testable improvement. Phases are ordered by ROI.

### Guiding Principles

| Principle | Application |
|-----------|-------------|
| **Strangler Fig** | Each phase wraps existing behavior. Old path remains behind feature flags until new path proves stable. |
| **Prompts = Logic** | Phase 1 (OODA) is pure prompt content — zero code. This is Artha's philosophy: prompt first, script only when unreliable. |
| **SOLID / SRP** | `artha_context.py` carries runtime state. `fact_extractor.py` extracts facts. No god-objects. |
| **Twelve-Factor** | Config under `harness.agentic:` namespace. State in `state/`. Ephemeral in `tmp/`. |
| **Backward Compatibility** | Every feature flag defaults to current behavior when disabled. |

---

## 2. AS-IS Analysis — Foundation Audit

### 2.1 Current Architecture (Post DA-1–5 + VM-0–5)

```
User ─► AI CLI (Claude Code / Copilot / Gemini CLI)
             │
             ├─ reads config/Artha.md (~15KB compact, §R command router)
             │    ├── generated by generate_identity.py (compact mode)
             │    └── Phase-routed workflow files: config/workflow/*.md
             │         ├── preflight.md  (⛩️ gate → Steps 0–2b)
             │         ├── fetch.md      (⛩️ gate → Steps 3–4e)
             │         ├── process.md    (⛩️ gate → Steps 5–7b)
             │         ├── reason.md     (⛩️ gate → Steps 8–11)
             │         └── finalize.md   (⛩️ gate → Steps 12–19b + 🔌 Health Block)
             │
             ├─ Deep Agents Harness (all feature-flagged):
             │    ├── context_offloader.py  (Step 4, 5, 7, 8 → tmp/)
             │    ├── domain_index.py       (Step 4b′ → progressive disclosure)
             │    ├── session_summarizer.py  (post-command context compression)
             │    ├── middleware/            (PII → WriteGuard → Audit → [write] → Verify)
             │    └── schemas/              (BriefingOutput, SessionSummary, DomainIndex)
             │
             ├─ VM Hardening:
             │    ├── detect_environment.py  (7-probe manifest, 5-min TTL)
             │    ├── audit_compliance.py    (7-check compliance scorer)
             │    └── preflight.py           (--advisory, profile check, token lifecycle)
             │
             ├─ prompts/*.md    (18+ domain prompts, lazy-loaded per domain_index)
             ├─ state/*.md      (31 files, 10 encrypted .age)
             └─ tmp/            (ephemeral offloaded artifacts, session histories)
```

### 2.2 What Works Well (Preserve These)

| Capability | Phase | Token Impact |
|------------|:-----:|:------------:|
| Tiered state loading (Tier A always, Tier B lazy) | DA-2 | -40–60% dormant domain tokens |
| Context offloading (5K+ token artifacts → tmp/) | DA-1 | -20–60K on heavy days |
| Session summarization (post-command compression) | DA-3 | -60–80% inter-command |
| Progressive domain disclosure (command-level routing) | DA-2 | -15K for `/status` |
| Middleware stack (composable state write guards) | DA-4 | N/A (quality, not tokens) |
| Structured output validation (non-blocking schemas) | DA-5 | N/A (consistency) |
| ⛩️ Phase gates + Silent Failure Summary Block | VM-4 | N/A (compliance + visibility) |
| Compact Artha.md (78KB → ~15KB) | VM-3 | -63K base context |

### 2.3 What's Missing — Gap Analysis

| Gap | Source Framework | Current State | Impact |
|-----|-----------------|---------------|--------|
| **No structured analysis protocol** | SK, LangGraph | Step 8 cross-domain reasoning is a single free-form pass. No Observe-Orient-Decide-Act loop. Root-cause resolution is ad-hoc. | Medium — compound signals inconsistently detected |
| **No eviction priority ordering** | Agno | `context_offloader.py` uses simple threshold (>5K tokens → offload). No priority: pipeline JSONL evicted with same urgency as critical alert context. | Medium — under extreme pressure, critical context may be evicted |
| **No typed runtime context** | Pydantic AI | Session state (command, environment, degradations, context pressure, active domains) is carried implicitly in conversation. No structured object that middleware/scripts can inspect. | Medium — fragile implicit state passing |
| **No step resumption** | LangGraph | If catch-up crashes at Step 7, restart runs everything from Step 0. No checkpoint-based recovery. | Low–Medium — wasted API calls + latency |
| **No persistent cross-session facts** | Agno | `session_summarizer.py` writes to `tmp/session_history_{N}.md` — cleaned at Step 18. Facts (patterns, preferences, corrections) are lost every session. | Medium — Artha never learns |
| **No sub-agent context isolation** | OpenAI SDK, AutoGen | All domains processed in shared context (Step 7). Finance extraction consumes tokens irrelevant to health. IDE sub-agent APIs now maturing. | Low–Medium — clean separation blocked by API maturity |

---

## 3. Pain Points — Next-Generation Gaps

### P1 — Inconsistent Cross-Domain Reasoning Quality

**Symptom:** Compound signals (immigration deadline × finance cashflow,
health appointment × calendar conflict) are detected on ~40% of applicable
catch-ups. No systematic protocol for the AI to follow during Step 8.

**Root cause:** Step 8 ("Cross-domain reasoning") in `config/workflow/reason.md`
provides a checklist of domain pairs to cross-reference but no structured
methodology. The AI's reasoning quality varies by context pressure, session
position, and model temperature.

**Framework parallel:** Semantic Kernel's "Kernel Loop": for each goal →
select function → execute → evaluate → loop-or-complete. LangGraph's
OODA-equivalent state machine: Observe (gather) → Orient (analyze) →
Decide (prioritize) → Act (output). Both enforce structured reasoning
that doesn't degrade under pressure.

**Measured impact:** `compound_signals_fired` averages 1.2 on heavy days
vs. 2.8 on light days (per harness_metrics). The reasoning step is the
bottleneck, not data availability.

### P2 — Context Eviction Under Extreme Pressure Has No Priority

**Symptom:** When context pressure reaches `red` or `critical`, the
offloader evicts artifacts based purely on token count threshold. A
60K-token pipeline dump and a 6K-token alert summary are treated equally.

**Root cause:** `context_offloader.py`'s `offload_artifact()` applies a
single `threshold_tokens` parameter. There's no concept of artifact
criticality or eviction priority.

**Framework parallel:** Agno's memory system uses tiered eviction:
`session_memory` (most volatile) → `run_memory` (per-task) →
`user_memory` (persistent). Each tier has different eviction thresholds
and preservation priorities.

**Measured impact:** On `critical` pressure days, the alerts list and ONE
THING context occasionally get evicted to tmp/ alongside raw pipeline
data, degrading briefing quality.

### P3 — No Cross-Session Learning

**Symptom:** Artha processes the same correction multiple times. "Don't
flag Costco visits as anomalous spending" corrected in Monday's session
is re-flagged on Tuesday. Extracted patterns (recurring bill dates,
school pickup schedule, doctor preferences) are session-ephemeral.

**Root cause:** `session_summarizer.py` writes `key_findings` and
`open_threads` to `tmp/session_history_{N}.md`, which Step 18 cleans up.
`state/memory.md` exists as a state file but has no structured fact schema
and is not populated by any automated extraction.

**Framework parallel:** Agno's `UserMemory` persists extracted facts across
sessions. Each session's key observations are compared against existing
facts; new facts added, contradictions flagged.

**Measured impact:** Users manually edit `state/memory.md` for recurring
corrections. No automation. Same false-positive alerts reoccur.

### P4 — No Step Resumption After Failures

**Symptom:** If a catch-up session crashes (network timeout, MCP error,
context overflow) at Step 7, the user must restart from scratch. Steps 0–6
(preflight, vault, pipeline, state loading, routing) re-execute even
though their outputs are already in `tmp/`.

**Root cause:** The catch-up workflow is stateless at the session level.
Each invocation starts from Step 0. The offloaded artifacts in `tmp/` exist
but the workflow doesn't check for them.

**Framework parallel:** LangGraph's `StateGraph` writes checkpoints after
each node. On failure, `graph.resume(checkpoint_id)` restarts from the
last successful node. The "implicit checkpoint" pattern: if the artifact
exists in the expected location, skip its production step.

**Measured impact:** Full catch-up restart costs 2–5 minutes of API time.
On rate-limited days, this can mean a 20–30 minute delay to complete a
catch-up.

### P5 — Fragile Implicit Runtime State

**Symptom:** Middleware modules, scripts, and the workflow all need to know
the current command, environment type, degradation list, context pressure
level, and active domains. This state is inferred, not declared. Different
components construct it independently, sometimes inconsistently.

**Root cause:** No typed runtime context object. `detect_environment.py`
returns `EnvironmentManifest`. `session_summarizer.py` has `SessionSummary`.
`preflight.py` returns `CheckResult` lists. But no unified object carries
these through the workflow.

**Framework parallel:** Pydantic AI's `RunContextWrapper[T]` — a typed
generic object injected into every tool call and agent step. Separates
"what the LLM sees" from "what the code sees." OpenAI Agents SDK's
`RunContextWrapper` pattern: context available to all tools without
polluting the LLM's context window.

**Measured impact:** Middleware currently infers `artha_dir` from path
convention. Environment detection results parsed separately by each
consumer. Health-check metrics assembled from scattered sources.

---

## 4. Framework Primitives — Extraction & Mapping

### 4.1 Primitive Extraction Matrix

| # | Primitive | Source Framework(s) | What We Take | What We Leave Behind |
|---|-----------|--------------------|--------------|-----------------------|
| **FP-1** | OODA Analysis Loop | Semantic Kernel (Kernel Loop), LangGraph (StateGraph cycles) | Structured 4-phase reasoning protocol: Observe → Orient → Decide → Act. Applied to Step 8 cross-domain reasoning. | SK's full planner infrastructure, LangGraph's graph compiler, node-based execution engine |
| **FP-2** | Tiered Eviction Priority | Agno (session/run/user memory tiers) | Priority constants per artifact type. Critical artifacts (alerts, ONE THING) evicted last. Pipeline dumps evicted first. | Agno's AgentMemory class hierarchy, database-backed storage, embedding-based retrieval |
| **FP-3** | Typed Runtime Context | Pydantic AI (`RunContextWrapper[T]`), OpenAI SDK (`RunContextWrapper`) | `ArthaContext` Pydantic model carrying command, environment, pressure, active domains, degradations. Injected into middleware + scripts. | Pydantic AI's agent framework, OpenAI SDK's agent loop, LLM-opaque injection mechanism |
| **FP-4** | Implicit Checkpoints | LangGraph (StateGraph checkpoints) | Step-resumption: check for existing `tmp/` artifacts before re-executing steps. Marker file `tmp/.checkpoint.json` tracks last completed step. | LangGraph's checkpoint serializer, thread-based isolation, PostgreSQL-backed state |
| **FP-5** | Persistent Fact Extraction | Agno (UserMemory), Semantic Kernel (SemanticMemory) | `fact_extractor.py`: after each catch-up, extract 3–7 durable facts from session summary → append to `state/memory.md` with structured schema. Dedup against existing facts. | Agno's embedding-based similarity search, vector store, classification models |
| **FP-6** | Handoff Context Compression | OpenAI Agents SDK (handoff `input_filter`, `nest_handoff_history`) | When delegating to sub-agent: compress prior conversation into summary, pass only domain-relevant context + state file. Agent-as-tool pattern for sandboxed sub-computation. | OpenAI's handoff routing, agent swarms, SDK runtime loop |

### 4.2 Priority Matrix

| Primitive | Effort | Risk | ROI | Dependencies | Priority |
|-----------|:------:|:----:|:---:|:------------:|:--------:|
| FP-1: OODA Analysis Loop | **Low** (prompt only) | **Low** | **High** (compound signal detection +) | None | **P0** |
| FP-2: Tiered Eviction | **Low** (~50 lines) | **Low** | **Medium** (critical context preserved) | context_offloader.py | **P0** |
| FP-3: ArthaContext | **Medium** (~200 lines) | **Medium** | **Medium** (unified runtime state) | middleware/, detect_environment | **P1** |
| FP-4: Implicit Checkpoints | **Medium** (~150 lines) | **Medium** | **Medium** (crash recovery) | context_offloader.py | **P1** |
| FP-5: Persistent Facts | **Medium** (~250 lines) | **Low** | **High** (cross-session learning) | session_summarizer.py | **P2** |
| FP-6: Sub-Agent Isolation | **High** (~400 lines) | **High** | **Medium** (per-domain isolation) | FP-3, IDE sub-agent API stability | **P3** |

---

## 5. Design Proposals & Trade-Off Matrices

### 5.1 OODA Analysis Loop — 3 Options

| Criterion | Option A: Prompt content in reason.md | Option B: Python OODA orchestrator | Option C: Pydantic-validated reasoning schema |
|-----------|:------:|:------:|:------:|
| Preserves "prompts = logic" (TS §1.1) | ✓ | ✗ Architecture violation | ✓ Partial |
| Implementation cost | **Zero code** | High (~400 lines) | Medium (~150 lines) |
| Compliance probability | ~75% (prompt-driven) | ~98% (deterministic) | ~85% (schema-enforced) |
| Fits Twelve-Factor | ✓ | ✗ Config becomes code | ✓ |
| Backward compatible | ✓ (additive content) | ✗ New execution path | ✓ (schema optional) |
| Measurable via audit_compliance | ✓ (check for OODA headers) | ✓ | ✓ |

**→ Decision: Option A (primary) + Option C (supplementary in future).**
The OODA loop is a reasoning protocol, not a code construct. Adding it to
`config/workflow/reason.md` as structured prompt content respects Artha's
philosophy. Post-hoc compliance audit (audit_compliance.py) verifies the
AI followed the protocol. Schema validation (Option C) deferred until
OODA compliance is measured below 70%.

### 5.2 Tiered Eviction Priority — 3 Options

| Criterion | Option A: Priority constants in offloader | Option B: External priority config (YAML) | Option C: Dynamic priority (context-pressure-adaptive) |
|-----------|:------:|:------:|:------:|
| Implementation cost | Low (~50 lines) | Medium (~100 lines + config) | High (~200 lines) |
| Flexibility | Fixed at code level | User-configurable | Auto-adaptive |
| Risk of misconfiguration | None | Medium (wrong priorities) | High (feedback loops) |
| Testability | High (deterministic) | Medium | Low |
| Matches current offloader API | ✓ (add `priority` param) | ✓ | ✗ (new API surface) |

**→ Decision: Option A.** Priority constants are simple, testable, and
sufficient. The artifact set is well-known (pipeline JSONL, processed
emails, domain extractions, cross-domain analysis, alerts). Fixed ordering
is correct. Dynamic adaptation adds complexity without proportional benefit
for a personal system with predictable artifact shapes.

### 5.3 ArthaContext — 3 Options

| Criterion | Option A: Pydantic model + builder function | Option B: TypedDict (no Pydantic) | Option C: Embedded in Artha.core.md (prompt-only) |
|-----------|:------:|:------:|:------:|
| Type safety | ✓ Runtime validation | ✓ Static only | ✗ None |
| Middleware integration | ✓ Clean injection | ✓ Dict-like access | ✗ Requires parsing |
| Code dependency | Pydantic (already in requirements) | stdlib only | Zero |
| Serializable to health-check | ✓ `.model_dump()` | ✓ Manual | ✗ |
| Testable | ✓ Schema validation | ✓ | ✗ |
| Cross-CLI compatible | ✓ Python scripts see it | ✓ | ✓ But no type safety |

**→ Decision: Option A.** Pydantic is already a dependency (DA-5 schemas).
`ArthaContext` is a foundational type — runtime validation catches
integration errors early. Builder function constructs it from preflight
results + environment manifest + config flags.

### 5.4 Implicit Checkpoints — 3 Options

| Criterion | Option A: Artifact-existence check (passive) | Option B: Explicit checkpoint file (active) | Option C: Full graph-based execution engine |
|-----------|:------:|:------:|:------:|
| Preserves "prompts = logic" | ✓ (prompt instructs AI to check tmp/) | ✓ (prompt + marker file) | ✗ Architecture change |
| Recovery granularity | Per-step (coarse) | Per-step (coarse) | Per-node (fine) |
| Implementation cost | Low (~20 lines prompt) | Medium (~100 lines) | High (~500+ lines) |
| Reliability | Medium (AI may not check) | High (deterministic) | High |
| Crash recovery | Check if `tmp/pipeline_output.jsonl` exists → skip Step 4 | Read `tmp/.checkpoint.json` → resume at step N | Full replay from checkpoint |

**→ Decision: Option B.** Passive artifact checks (Option A) are
unreliable — the AI may not check. Explicit `tmp/.checkpoint.json`
created after each major step gives deterministic resumption points.
The prompt instructs the AI to read the checkpoint file first. Full
graph engine (Option C) violates "prompts = logic" and is over-
engineered for a 21-step linear workflow.

### 5.5 Persistent Fact Extraction — 3 Options

| Criterion | Option A: Post-session extraction script | Option B: Inline during Step 8 reasoning | Option C: Embedding-based semantic memory |
|-----------|:------:|:------:|:------:|
| Preserves session isolation | ✓ (runs after session actions) | ✗ (interleaves reasoning + storage) | ✓ |
| Implementation cost | Medium (~250 lines) | Low (~50 lines prompt) | High (~500+ lines + vector DB) |
| Extraction quality | High (script parses session summary) | Medium (AI infers in-flight) | High (semantic matching) |
| Deduplication | ✓ (compare against existing memory.md) | ✗ (no comparison during reasoning) | ✓ (embedding similarity) |
| Dependencies | session_summarizer.py | None | Vector database, embedding model |

**→ Decision: Option A.** The extraction runs after the session summary
is generated (post-command), reads `tmp/session_history_{N}.json`,
compares against existing `state/memory.md` facts, and appends new
durable facts. Clean separation of concerns. Embedding-based search
(Option C) is overkill for a personal system with hundreds of facts,
not millions.

### 5.6 Sub-Agent Context Isolation — 2 Options

| Criterion | Option A: IDE sub-agent API (runSubagent) | Option B: Prompt-simulated isolation |
|-----------|:------:|:------:|
| True context isolation | ✓ Separate context window | ✗ Shared window |
| Token savings | High (per-domain isolation) | None |
| IDE dependency | VS Code / Claude Code API | None |
| Cross-CLI compatible | ✗ VS Code only (currently) | ✓ |
| Handoff compression | ✓ Input filter strips irrelevant context | N/A |

**→ Decision: Option A, deferred to P3.** True sub-agent isolation
requires IDE API maturity. VS Code's `runSubagent` exists but is not
stable across Copilot/Claude Code/Gemini CLI. Monitor API stabilization.
When ready, adopt OpenAI SDK's handoff input filter pattern: compress
conversation history into a domain-specific summary before delegation.

---

## 6. Execution Plan — Strangler Fig Migration

### Execution Sequencing

```
Phase 1 (OODA) ───────────────────▶ (zero code, prompt-only)
Phase 2 (Tiered Eviction) ────────▶ (enhances context_offloader.py)
         ╲
          Phase 3 (ArthaContext) ──▶ (new module, benefits from Phase 2)
                   ╲
                    Phase 4 ──────▶ (checkpoints, benefits from Phase 3)
                         ╲
                          Phase 5 ▶ (persistent facts, benefits from Phase 3+4)
                                    ╲
                                Future: Phase 6 ▶ (sub-agent isolation)
```

Phases 1 and 2 are parallel-safe (no dependencies).
Phases 3–5 are sequential — each builds on the prior phase.
Phase 6 is deferred pending IDE API stabilization.

---

### Phase 1: OODA Analysis Loop (Prompt-Only)

**Goal:** Replace the ad-hoc cross-domain reasoning in Step 8 with a
structured 4-phase OODA protocol. Increase compound signal detection
by 40–60% with zero code changes.

**Inspired by:** Semantic Kernel's Kernel Loop (plan → execute →
evaluate → iterate), LangGraph's cyclic state graphs (observe state →
determine action → execute → evaluate completion).

**Scope:**

| Component | Current | New |
|-----------|---------|-----|
| Step 8 reasoning | Free-form "check these domain pairs" list | Structured OODA protocol: 4 explicit phases with outputs |
| Reasoning output | Inline in context | Each OODA phase produces named output section |
| Compound signal quality | ~1.2 per heavy-day catch-up | Target: 2.5+ |
| Self-evaluation | None | OODA-Act phase includes "validate against evidence" step |

**Implementation:**

Add the following OODA protocol to `config/workflow/reason.md`, replacing
the existing Step 8 cross-domain reasoning section:

```markdown
## Step 8 — Cross-Domain Reasoning (OODA Protocol)

For EVERY catch-up, execute the following 4-phase protocol.
Do NOT skip any phase. Each phase produces a named output block.

### 8-O: OBSERVE — Gather Evidence

Collect ALL signals from the completed domain processing:
- List every alert triggered (with severity and domain)
- List every state file mutation (what changed, delta magnitude)
- List every anomaly detected (unusual patterns, thresholds crossed)
- Note which domains had NO activity (absence is signal)

Output: `[OBSERVE]` block with structured signal inventory.

### 8-Or: ORIENT — Analyze Cross-Domain Connections

For EACH mandatory cross-domain pair below, explicitly state whether
a connection exists and what it implies:

| Pair | Why Check |
|------|-----------|
| Immigration × Finance | Visa fees, status changes affect employment authorization |
| Immigration × Calendar | Filing deadlines, interview dates, document expiry |
| Kids/School × Calendar | School events, parent-teacher, activity conflicts |
| Health × Calendar | Appointments, prescription refills, lab follow-ups |
| Travel × Immigration | Visa validity for travel, re-entry requirements |
| Finance × Home | Property tax, utility anomalies, maintenance costs |
| Employment × Finance | Payroll changes, benefits enrollment, RSU vesting |
| Goals × [All Domains] | Which goals made progress? Which are stalled? |

For each pair: "Connection found: [description]" or "No connection."

Additionally, check for COMPOUND SIGNALS — situations where 2+ domains
together imply something that neither domain alone would surface:
- Example: Immigration filing receipt + Finance large wire = filing fee paid
- Example: Health appointment + Calendar conflict = needs rescheduling
- Example: Kids school break + Travel booking = family trip context

Output: `[ORIENT]` block with connection matrix and compound signals.

### 8-D: DECIDE — Prioritize Actions

From the ORIENT output, rank ALL findings by urgency × impact × actionability:
1. What requires action TODAY? (🔴 Critical)
2. What requires action THIS WEEK? (🟠 Urgent)
3. What is informational but notable? (🟡 Standard)
4. What is low-priority? (🔵 Info)

Select the ONE THING — the single most important insight or action.
The ONE THING must be:
- Actionable (user can do something about it)
- Time-sensitive (matters NOW, not next month)
- High-impact (affects a high-stakes domain or multiple domains)

Output: `[DECIDE]` block with prioritized action list and ONE THING selection.

### 8-A: ACT — Validate and Output

Before finalizing the briefing, validate decisions against evidence:
- Does each alert cite a specific data source (email, calendar event, state file)?
- Are there any claims not grounded in observed data? ← REMOVE THESE
- Does the ONE THING pass the "so what?" test? (If removed, would the user miss it?)
- Cross-check: any domain that had activity but produced zero alerts — re-examine.

If validation fails, cycle back to ORIENT with the failed item.
Maximum cycles: 2. After 2 cycles, proceed with best-effort output.

Output: Validated findings flow into Step 9 (briefing synthesis).
```

**Compliance check (audit_compliance.py):**

Add to the existing compliance checks in `audit_compliance.py`:

```python
def check_ooda_protocol(briefing_text: str) -> CheckResult:
    """Check that OODA protocol headers are present in the reasoning output."""
    ooda_markers = ["[OBSERVE]", "[ORIENT]", "[DECIDE]", "[ACT]"]
    # Check in offloaded cross-domain analysis if available
    found = sum(1 for m in ooda_markers if m in briefing_text)
    passed = found >= 3  # Allow 1 missing (AI may merge DECIDE+ACT)
    return CheckResult(
        "ooda_protocol", passed, weight=10,
        detail=f"OODA phases found: {found}/4"
    )
```

**Files changed:**

| File | Change | Risk |
|------|--------|:----:|
| `config/workflow/reason.md` | Replace Step 8 content with OODA protocol | Med |
| `scripts/audit_compliance.py` | Add `check_ooda_protocol()` (~20 lines) | Low |
| `tests/unit/test_audit_compliance.py` | Add OODA check tests | — |

**Rollback:** Revert `reason.md` Step 8 content to the prior checklist.
Remove `check_ooda_protocol()`.

**Exit criteria:**
- OODA protocol present in `config/workflow/reason.md`
- `audit_compliance.py` checks for `[OBSERVE]`, `[ORIENT]`, `[DECIDE]`, `[ACT]` markers
- Next 5 catch-ups produce compound_signals ≥ 2 on average
- `check_ooda_protocol` passes for ≥3 out of 5 catch-ups

**Risks:**

| Risk | L | I | Mitigation |
|------|:-:|:-:|------------|
| AI ignores OODA protocol sections | M | M | ⛩️ Phase gate at top of reason.md already enforces structure. OODA markers are short, imperative. Compliance audit catches misses. |
| OODA protocol adds tokens to Step 8 | L | L | Protocol is ~800 tokens. Step 8 already uses 5K–15K. Marginal increase (~5%). |
| Cycling back from ACT to ORIENT adds latency | L | L | Max 2 cycles. Typical: 0 cycles (first-pass valid). |

---

### Phase 2: Tiered Eviction Priority

**Goal:** Add priority ordering to context offloading. Critical artifacts
(alerts, ONE THING, compound signals) preserved in context; low-value
artifacts (raw pipeline JSONL, intermediate extractions) evicted first.

**Inspired by:** Agno's memory tiering — `session_memory` (high churn) →
`run_memory` (per-task, medium) → `user_memory` (persistent, low churn).
Different eviction thresholds per tier.

**Scope:**

| Artifact | Current Priority | New Priority | Eviction Behavior |
|----------|:----------------:|:------------:|-------------------|
| Pipeline JSONL output | None (flat) | **P3 — Ephemeral** | Evict first, always offload above 2K tokens |
| Processed email batch | None | **P3 — Ephemeral** | Evict first, always offload above 2K tokens |
| Domain extraction results | None | **P2 — Intermediate** | Offload above 5K tokens (current default) |
| Cross-domain scoring detail | None | **P2 — Intermediate** | Offload above 5K tokens |
| Alert list + ONE THING | None | **P1 — Critical** | Never offload; keep in context always |
| Compound signals | None | **P1 — Critical** | Never offload; keep in context always |
| Session summary card | None | **P0 — Pinned** | Never offload; always in context |

**Implementation:**

Modify `scripts/context_offloader.py`:

```python
"""Enhanced eviction tiers for context offloading."""

from enum import IntEnum


class EvictionTier(IntEnum):
    """Lower value = higher preservation priority (evicted LAST)."""
    PINNED = 0       # Never evicted: session summary card
    CRITICAL = 1     # Never evicted under normal pressure: alerts, ONE THING
    INTERMEDIATE = 2 # Standard threshold: domain extractions, scoring
    EPHEMERAL = 3    # Evict first, aggressive threshold: pipeline JSONL, raw emails


# Threshold multipliers per tier (applied to base threshold_tokens)
_TIER_THRESHOLDS: dict[EvictionTier, float] = {
    EvictionTier.PINNED: float("inf"),       # Never offload
    EvictionTier.CRITICAL: float("inf"),     # Never offload under normal pressure
    EvictionTier.INTERMEDIATE: 1.0,          # Use base threshold (5K default)
    EvictionTier.EPHEMERAL: 0.4,             # Aggressive: 2K (40% of base)
}

# Default tier assignments for known artifact names
_ARTIFACT_TIERS: dict[str, EvictionTier] = {
    "pipeline_output": EvictionTier.EPHEMERAL,
    "processed_emails": EvictionTier.EPHEMERAL,
    "domain_extractions": EvictionTier.INTERMEDIATE,
    "cross_domain_analysis": EvictionTier.INTERMEDIATE,
    "alerts": EvictionTier.CRITICAL,
    "one_thing": EvictionTier.CRITICAL,
    "compound_signals": EvictionTier.CRITICAL,
    "session_summary": EvictionTier.PINNED,
}
```

Update `offload_artifact()` signature:

```python
def offload_artifact(
    name: str,
    data: Any,
    summary_fn: Callable[[Any], str],
    *,
    threshold_tokens: int = 5_000,
    preview_lines: int = 10,
    artha_dir: Path | None = None,
    tier: EvictionTier | None = None,  # NEW: explicit tier override
) -> str:
    """Write data to tmp/{name}.json; return summary card if data > tier-adjusted threshold.

    If tier is None, looks up name in _ARTIFACT_TIERS (default: INTERMEDIATE).
    Tier determines the effective threshold: base * _TIER_THRESHOLDS[tier].
    PINNED and CRITICAL tiers never offload under normal conditions.
    """
```

Add critical-pressure override: when `context_pressure: critical` in
health-check, CRITICAL tier artifacts ARE offloaded (but with full
summary cards preserving all alert data). PINNED artifacts never offload.

**Artha.core.md changes:**

Update the harness_metrics section (Step 16) to log eviction tier stats:

```yaml
harness_metrics:
  context_offloading:
    artifacts_offloaded: 3
    tokens_offloaded: 42000
    eviction_tiers:           # NEW
      ephemeral_count: 2
      intermediate_count: 1
      critical_count: 0
      pinned_count: 0
```

**Files changed:**

| File | Change | Risk |
|------|--------|:----:|
| `scripts/context_offloader.py` | Add `EvictionTier`, `_ARTIFACT_TIERS`, update `offload_artifact()` | Low |
| `config/Artha.core.md` | Update harness_metrics eviction section | Low |
| `config/artha_config.yaml` | Add `harness.agentic.tiered_eviction.enabled: true` | Low |
| `tests/unit/test_context_offloader.py` | Add tier-based tests | — |

**Tests:**
- `test_ephemeral_offloads_at_lower_threshold`: Pipeline output offloads at 2K, not 5K
- `test_critical_never_offloads_normal_pressure`: Alerts stay in context at green/yellow/red
- `test_critical_offloads_at_critical_pressure`: Under `critical` pressure, CRITICAL tier offloads with full card
- `test_pinned_never_offloads`: Session summary never offloaded regardless of pressure
- `test_unknown_artifact_defaults_intermediate`: Unregistered names use INTERMEDIATE tier
- `test_explicit_tier_overrides_lookup`: `tier=` parameter overrides `_ARTIFACT_TIERS`
- `test_feature_flag_disabled_flat_threshold`: When disabled, all artifacts use base threshold (backward compat)

**Rollback:** Set `harness.agentic.tiered_eviction.enabled: false`. All
offloading reverts to flat threshold behavior.

**Exit criteria:**
- `EvictionTier` enum and `_ARTIFACT_TIERS` mapping in context_offloader.py
- `offload_artifact()` accepts `tier` parameter
- Under normal pressure, alert context stays in-context (never in tmp/)
- Under critical pressure, only PINNED artifacts remain in-context
- harness_metrics logs `eviction_tiers` sub-section
- All existing offloader tests pass (backward compat)

**Risks:**

| Risk | L | I | Mitigation |
|------|:-:|:-:|------------|
| Keeping CRITICAL in-context adds tokens on heavy days | M | L | CRITICAL artifacts are small (alerts: ~500 tokens, ONE THING: ~100 tokens). Net savings from EPHEMERAL aggressive eviction exceeds this. |
| Wrong artifact assigned to wrong tier | L | M | `_ARTIFACT_TIERS` is a simple lookup with sensible defaults. Code review catches misassignment. |

---

### Phase 3: ArthaContext Typed Runtime Context

**Goal:** Create a typed Pydantic model that carries runtime state
through the workflow. Eliminates implicit state passing between middleware,
scripts, and prompt logic.

**Inspired by:** Pydantic AI's `RunContextWrapper[T]` — a typed generic
injected into every tool call. OpenAI SDK's `RunContextWrapper` — context
available to tools without polluting the LLM context window.

**Key design decision:** `ArthaContext` is a **code-side** object. It is
NOT injected into the LLM's context window. The AI sees its effects (e.g.,
"read-only mode active") through workflow file instructions, not through
a serialized context dump. This respects the separation between "what
the code knows" and "what the LLM sees" (Pydantic AI's core insight).

**Scope:**

| Consumer | Current State Access | New State Access |
|----------|---------------------|------------------|
| `middleware/` | `artha_dir` inferred from Path convention | `ctx.artha_dir`, `ctx.environment`, `ctx.is_degraded` |
| `context_offloader.py` | Config read inline from `artha_config.yaml` | `ctx.config.harness`, `ctx.pressure` |
| `session_summarizer.py` | Context estimated from char count | `ctx.pressure`, `ctx.command`, `ctx.step` |
| `audit_compliance.py` | Parses briefing text for signals | `ctx.steps_executed`, `ctx.connectors_online` |
| `preflight.py` | Returns raw `CheckResult` list | Populates `ctx.preflight_results` |

**Implementation:**

1. **New module:** `scripts/artha_context.py`

```python
"""
scripts/artha_context.py — Typed runtime context for Artha workflows.

Carries runtime state through the catch-up workflow. Constructed by
build_context() at the start of a session. Updated by preflight,
pipeline, and middleware as the workflow progresses.

The LLM never sees this object directly. It informs code-side decisions
(middleware gating, eviction tiers, checkpoint logic).
"""

from __future__ import annotations

from dataclasses import field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ContextPressure(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    CRITICAL = "critical"


class ConnectorStatus(BaseModel):
    name: str
    online: bool
    last_error: str | None = None


class ArthaContext(BaseModel):
    """Immutable-ish runtime context for a single Artha session."""

    # Session identity
    command: str = Field(description="Current command: catch-up, status, items, etc.")
    session_id: str = Field(description="Unique session identifier (ISO timestamp)")
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Environment (from detect_environment.py)
    environment: str = Field(default="local_mac", description="local_mac|local_windows|cowork_vm|unknown")
    is_degraded: bool = Field(default=False, description="True if read-only or missing capabilities")
    degradations: list[str] = Field(default_factory=list)

    # Preflight results
    preflight_passed: bool = Field(default=False)
    advisory_mode: bool = Field(default=False)
    connectors: list[ConnectorStatus] = Field(default_factory=list)

    # Workflow progress
    current_step: int = Field(default=0, description="Last completed step number")
    steps_executed: list[int] = Field(default_factory=list)

    # Context pressure
    pressure: ContextPressure = Field(default=ContextPressure.GREEN)
    tokens_estimated: int = Field(default=0)
    tokens_offloaded: int = Field(default=0)

    # Domain routing
    active_domains: list[str] = Field(default_factory=list)
    volume_tier: str = Field(default="standard", description="standard|medium|high|extreme")

    # Paths
    artha_dir: Path = Field(default=Path("."))

    class Config:
        use_enum_values = True


def build_context(
    command: str,
    artha_dir: Path,
    preflight_results: list[Any] | None = None,
    env_manifest: dict[str, Any] | None = None,
) -> ArthaContext:
    """Construct ArthaContext from available session data.

    Called once at session start (after preflight, before Step 3).
    Consumes outputs from detect_environment.py and preflight.py.
    """
    ctx = ArthaContext(
        command=command,
        session_id=datetime.now(timezone.utc).isoformat(),
        artha_dir=artha_dir,
    )

    if env_manifest:
        ctx.environment = env_manifest.get("environment", "unknown")
        ctx.is_degraded = not env_manifest.get("capabilities", {}).get("filesystem_writable", True)
        ctx.degradations = env_manifest.get("degradations", [])

    if preflight_results:
        ctx.preflight_passed = all(r.passed for r in preflight_results if r.priority == "P0")
        ctx.advisory_mode = any(getattr(r, "advisory", False) for r in preflight_results)
        ctx.connectors = [
            ConnectorStatus(
                name=r.name,
                online=r.passed,
                last_error=r.detail if not r.passed else None,
            )
            for r in preflight_results
            if "token" in r.name.lower() or "connector" in r.name.lower()
        ]

    return ctx
```

2. **Middleware integration:**

Update `scripts/middleware/__init__.py` `StateMiddleware` protocol to
accept optional context:

```python
class StateMiddleware(Protocol):
    def before_write(
        self, domain: str, current_content: str, proposed_content: str,
        ctx: ArthaContext | None = None,
    ) -> str | None: ...

    def after_write(
        self, domain: str, file_path: Path,
        ctx: ArthaContext | None = None,
    ) -> None: ...
```

**Backward compatible:** `ctx` defaults to `None`. Existing callers
unchanged. Middleware implementations that don't use `ctx` are unaffected.

3. **Context serialization to health-check.md:**

At Step 16, serialize `ArthaContext.model_dump()` subset to `harness_metrics`:

```yaml
harness_metrics:
  session:
    command: catch-up
    environment: local_mac
    is_degraded: false
    pressure: yellow
    steps_executed: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    active_domains: [immigration, finance, kids, calendar, comms]
    volume_tier: medium
    connectors_online: 4
    connectors_offline: 1
```

**Files changed:**

| File | Change | Risk |
|------|--------|:----:|
| `scripts/artha_context.py` | **Create** (~200 lines) | Low |
| `scripts/middleware/__init__.py` | Add `ctx` param to Protocol (backward compat) | Low |
| `config/artha_config.yaml` | Add `harness.agentic.context.enabled: true` | Low |
| `config/Artha.core.md` | Update harness_metrics schema with session sub-section | Low |
| `tests/unit/test_artha_context.py` | **Create** | — |

**Tests:**
- `test_build_context_defaults`: Default context is green pressure, local_mac, no degradations
- `test_build_context_from_env_manifest`: Cowork VM manifest → is_degraded=True
- `test_build_context_from_preflight`: Failed P0 → preflight_passed=False
- `test_middleware_backward_compat`: Existing middleware calls without `ctx` still work
- `test_context_serialization`: `model_dump()` produces valid dict for health-check
- `test_context_pressure_propagation`: Setting pressure affects eviction tier behavior

**Rollback:** Set `harness.agentic.context.enabled: false`. Delete
`artha_context.py`. Revert middleware protocol (remove `ctx` param).

**Exit criteria:**
- `ArthaContext` model passes schema validation tests
- `build_context()` consumes detect_environment + preflight outputs
- Middleware accepts optional `ctx` parameter without breaking existing callers
- health-check.md includes `session:` sub-section in harness_metrics
- All existing middleware tests pass unchanged

**Risks:**

| Risk | L | I | Mitigation |
|------|:-:|:-:|------------|
| ArthaContext becomes a god-object | M | M | Strict SRP: context carries state, doesn't decide. Decision logic stays in middleware/prompts. Model validation rejects unexpected fields. |
| Middleware backward compat broken | L | H | `ctx=None` default. All callers tested. Protocol uses `|None` optional. |
| Context drift from actual state | L | M | `build_context()` is the single constructor. No manual instantiation. Updated at known points (post-preflight, post-pipeline, post-routing). |

---

### Phase 4: Implicit Checkpoints & Step Resumption

**Goal:** After each major workflow step, write a checkpoint marker to
`tmp/.checkpoint.json`. On session start, check for existing checkpoints
and resume from the last successful step.

**Inspired by:** LangGraph's `StateGraph` checkpoints — each node writes
state after execution. On failure, `graph.resume(checkpoint_id)` restarts
from the last successful node without re-executing completed steps.

**Scope:**

| Step | Artifact in tmp/ | Checkpoint Value |
|------|-----------------|------------------|
| Step 0 (preflight) | `tmp/.env_manifest.json` (already exists) | `{"last_step": 0, "preflight_passed": true}` |
| Step 1 (vault) | Side-effect only | `{"last_step": 1, "vault_decrypted": true}` |
| Step 4 (pipeline) | `tmp/pipeline_output.jsonl` (DA-1) | `{"last_step": 4, "email_count": N}` |
| Step 4b (state load) | State files read | `{"last_step": 4.5, "domains_loaded": [...]}` |
| Step 7 (process) | `tmp/domain_extractions/` (DA-1) | `{"last_step": 7, "domains_processed": [...]}` |
| Step 8 (reason) | `tmp/cross_domain_analysis.json` (DA-1) | `{"last_step": 8, "ooda_completed": true}` |
| Step 11 (briefing) | `tmp/briefing_structured.json` (DA-5) | `{"last_step": 11}` |

**Implementation:**

1. **New module:** `scripts/checkpoint.py`

```python
"""
scripts/checkpoint.py — Step checkpoint tracking for crash recovery.

Writes a lightweight marker after each major workflow step.
On session start, reads the marker to determine resume point.
Checkpoint is ephemeral (tmp/) — cleaned at Step 18 like all tmp/ artifacts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_CHECKPOINT_FILE = "tmp/.checkpoint.json"
_MAX_AGE_HOURS = 4  # Stale checkpoints ignored (fresh session likely intended)


def read_checkpoint(artha_dir: Path) -> dict[str, Any] | None:
    """Read the current checkpoint state, or None if absent/stale."""
    path = artha_dir / _CHECKPOINT_FILE
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    # Reject stale checkpoints
    ts = data.get("timestamp", "")
    if ts:
        try:
            cp_time = datetime.fromisoformat(ts)
            if cp_time.tzinfo is None:
                cp_time = cp_time.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - cp_time).total_seconds() / 3600
            if age_hours > _MAX_AGE_HOURS:
                return None
        except (ValueError, TypeError):
            return None

    return data


def write_checkpoint(
    artha_dir: Path,
    step: float,
    **metadata: Any,
) -> None:
    """Write checkpoint after successful step completion."""
    path = artha_dir / _CHECKPOINT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "last_step": step,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **metadata,
    }
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def clear_checkpoint(artha_dir: Path) -> None:
    """Remove checkpoint file (called at Step 18 cleanup)."""
    path = artha_dir / _CHECKPOINT_FILE
    path.unlink(missing_ok=True)
```

2. **Artha.core.md / workflow file changes:**

Add to `config/workflow/preflight.md` (before Step 0):

```markdown
### Step 0a — Check for Resumable Session

Before running preflight, check for a previous checkpoint:
```
python -c "from scripts.checkpoint import read_checkpoint; import json; cp = read_checkpoint(Path('.')); print(json.dumps(cp) if cp else 'none')"
```

If a checkpoint exists and is <4 hours old:
- Report: "📍 Resumable session found — last completed Step {N}"
- Ask user: "Resume from Step {N+1}? (y/n)"
  - If yes: skip Steps 0 through N, load existing tmp/ artifacts
  - If no: clear checkpoint, start fresh
- Auto-resume (no prompt) if running in pipeline/automated mode

If no checkpoint or stale (>4h): proceed normally from Step 0.
```

Add checkpoint writes after each major step in workflow files:

```markdown
<!-- In workflow/fetch.md, after Step 4 completion: -->
**Checkpoint:** Write `python -c "from scripts.checkpoint import write_checkpoint; write_checkpoint(Path('.'), 4, email_count=N)"`

<!-- In workflow/process.md, after Step 7 completion: -->
**Checkpoint:** Write `python -c "from scripts.checkpoint import write_checkpoint; write_checkpoint(Path('.'), 7, domains_processed=[...])"'`
```

Add to `config/workflow/finalize.md` Step 18 cleanup:

```markdown
- Clear checkpoint: `python -c "from scripts.checkpoint import clear_checkpoint; clear_checkpoint(Path('.'))"`
```

3. **context_offloader.py integration:**

The checkpoint system works WITH the existing offloading:
- Step 4 writes `tmp/pipeline_output.jsonl` (DA-1)
- Checkpoint records Step 4 complete
- On resume: Step 3–4 skipped, AI reads `tmp/pipeline_output.jsonl` directly
- This is the "implicit checkpoint" pattern: artifact existence + marker = skip

**Files changed:**

| File | Change | Risk |
|------|--------|:----:|
| `scripts/checkpoint.py` | **Create** (~80 lines) | Low |
| `config/workflow/preflight.md` | Add Step 0a (checkpoint check) | Low |
| `config/workflow/fetch.md` | Add checkpoint write after Step 4 | Low |
| `config/workflow/process.md` | Add checkpoint write after Step 7 | Low |
| `config/workflow/reason.md` | Add checkpoint write after Step 8 | Low |
| `config/workflow/finalize.md` | Add checkpoint clear at Step 18 | Low |
| `config/artha_config.yaml` | Add `harness.agentic.checkpoints.enabled: true` | Low |
| `scripts/context_offloader.py` | Add `.checkpoint.json` to `OFFLOADED_FILES` cleanup list | Low |
| `tests/unit/test_checkpoint.py` | **Create** | — |

**Tests:**
- `test_write_read_checkpoint`: Write → read returns same data
- `test_stale_checkpoint_ignored`: >4h old checkpoint returns None
- `test_missing_checkpoint_returns_none`: No file → None
- `test_corrupt_checkpoint_returns_none`: Invalid JSON → None
- `test_clear_removes_file`: clear_checkpoint() deletes the marker
- `test_clear_missing_no_error`: clear_checkpoint() on missing file is safe

**Rollback:** Set `harness.agentic.checkpoints.enabled: false`. Remove
checkpoint writes from workflow files. Delete `checkpoint.py`. DA-1
offloaded artifacts continue to work independently.

**Exit criteria:**
- `checkpoint.py` passes all unit tests
- Workflow files include checkpoint writes at Steps 4, 7, 8
- Step 0a checkpoint check in preflight.md
- Step 18 cleanup clears checkpoint
- Manual test: crash at Step 7, resume completes from Step 8

**Risks:**

| Risk | L | I | Mitigation |
|------|:-:|:-:|------------|
| AI doesn't execute checkpoint write commands | M | M | Checkpoint writes are simple `python -c` one-liners. Compliance audit can check for `.checkpoint.json` existence. Phase gate enforcement helps. |
| Stale checkpoint resumes with outdated data | L | H | 4-hour TTL. Pipeline output has timestamps. Session summarizer has timestamp. User prompted before resume. |
| Checkpoint file left behind after crash | L | L | Next fresh session ignores stale checkpoints. Manual cleanup: `rm tmp/.checkpoint.json`. |
| Read-only mode can't write checkpoints | L | L | Checkpoint is in `tmp/` which is writable even in Cowork VM (tmpfs). If truly read-only, checkpoints silently skipped. |

---

### Phase 5: Persistent Fact Extraction

**Goal:** After each catch-up, extract durable facts from the session and
persist them to `state/memory.md`. Facts survive across sessions, enabling
Artha to learn patterns, remember corrections, and avoid repeated mistakes.

**Inspired by:** Agno's `UserMemory` — extracts structured facts from
sessions, deduplicates against existing memory, persists across runs.
Semantic Kernel's `SemanticMemory` — key-value factual memory with
relevance scoring.

**Key design decision:** Facts are stored in `state/memory.md` as
structured YAML frontmatter + Markdown body. No vector database. No
embeddings. Pattern matching for deduplication uses exact-match on
canonical fact identifiers. This is a personal system with hundreds
of facts, not millions.

**Scope:**

| Fact Type | Example | Source | TTL |
|-----------|---------|--------|:---:|
| **Correction** | "Costco purchases are not anomalous spending" | User correction during catch-up | Indefinite |
| **Pattern** | "Bills from PSE typically arrive on the 15th" | Observed across 3+ catch-ups | 180 days |
| **Preference** | "User prefers flash briefings on weekdays" | User behavior | Indefinite |
| **Contact** | "Dr. Smith is at Overlake Medical, (425) 555-0100" | Extracted from emails | 365 days |
| **Schedule** | "Arjun has soccer practice Tue/Thu 4pm" | Calendar pattern | 90 days |
| **Threshold** | "User considers >$500 grocery trip unusual" | User calibration | Indefinite |

**Implementation:**

1. **New module:** `scripts/fact_extractor.py`

```python
"""
scripts/fact_extractor.py — Persistent fact extraction for cross-session learning.

Runs after session summarization (post-command). Reads the session summary
from tmp/session_history_{N}.json, extracts durable facts, deduplicates
against existing state/memory.md facts, and appends new facts.

Facts are structured entries in state/memory.md frontmatter:

    facts:
      - id: "correction-costco-spending"
        type: correction
        domain: finance
        content: "Costco purchases are expected weekly spending, not anomalies"
        source: "catch-up 2026-03-15, user correction"
        created: "2026-03-15"
        ttl_days: null  # indefinite
        confidence: 1.0
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from pydantic import BaseModel, Field


class Fact(BaseModel):
    """A single durable fact extracted from a session."""
    id: str = Field(description="Canonical identifier: {type}-{domain}-{slug}")
    type: str = Field(description="correction|pattern|preference|contact|schedule|threshold")
    domain: str = Field(description="Artha domain this fact applies to")
    content: str = Field(max_length=500, description="Human-readable fact statement")
    source: str = Field(description="Where this fact was extracted from")
    created: str = Field(description="ISO date of extraction")
    ttl_days: int | None = Field(default=None, description="Days until expiry; null=indefinite")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class FactMemory(BaseModel):
    """Schema for the facts section of state/memory.md frontmatter."""
    facts: list[Fact] = Field(default_factory=list)


def extract_facts_from_summary(
    summary_path: Path,
    artha_dir: Path,
) -> list[Fact]:
    """Extract durable facts from a session summary JSON file.

    Reads the session summary, identifies factual assertions that should
    persist, and returns them as Fact objects.

    Extraction heuristics:
    1. User corrections: "Don't flag X as Y" → correction fact
    2. Observed patterns: "X happens every Y" → pattern fact
    3. Contact info: names + phones/emails + locations → contact fact
    4. Schedule patterns: recurring events → schedule fact
    """
    ...  # Implementation extracts structured data from summary JSON


def load_existing_facts(artha_dir: Path) -> list[Fact]:
    """Load existing facts from state/memory.md frontmatter."""
    memory_path = artha_dir / "state" / "memory.md"
    if not memory_path.exists():
        return []

    text = memory_path.read_text(encoding="utf-8")
    # Parse YAML frontmatter
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return []

    frontmatter = yaml.safe_load(match.group(1)) or {}
    raw_facts = frontmatter.get("facts", [])
    return [Fact(**f) for f in raw_facts if isinstance(f, dict)]


def deduplicate_facts(
    new_facts: list[Fact],
    existing_facts: list[Fact],
) -> list[Fact]:
    """Remove facts that already exist (by id) or are semantically equivalent."""
    existing_ids = {f.id for f in existing_facts}
    unique = []
    for fact in new_facts:
        if fact.id not in existing_ids:
            unique.append(fact)
        else:
            # Update confidence if higher
            for ef in existing_facts:
                if ef.id == fact.id and fact.confidence > ef.confidence:
                    ef.confidence = fact.confidence
                    ef.source = fact.source  # Update source to latest
    return unique


def persist_facts(
    new_facts: list[Fact],
    artha_dir: Path,
) -> int:
    """Append new facts to state/memory.md frontmatter. Returns count added."""
    if not new_facts:
        return 0

    memory_path = artha_dir / "state" / "memory.md"
    existing = load_existing_facts(artha_dir)
    unique = deduplicate_facts(new_facts, existing)

    if not unique:
        return 0

    # Expire old facts
    today = date.today()
    active = [
        f for f in existing
        if f.ttl_days is None or
        (date.fromisoformat(f.created) - today).days < f.ttl_days
    ]

    all_facts = active + unique

    # Rebuild frontmatter
    text = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
    body_match = re.match(r"^---\n.*?\n---\n?(.*)", text, re.DOTALL)
    body = body_match.group(1) if body_match else ""

    frontmatter = {
        "domain": "memory",
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "schema_version": "2.0",
        "facts": [f.model_dump() for f in all_facts],
    }

    new_text = "---\n" + yaml.dump(frontmatter, default_flow_style=False, sort_keys=False) + "---\n" + body
    memory_path.write_text(new_text, encoding="utf-8")

    return len(unique)
```

2. **Artha.core.md / workflow changes:**

Add to `config/workflow/finalize.md` after Step 11b (briefing validation)
and before Step 12 (alerts):

```markdown
### Step 11c — Persistent Fact Extraction

After the session summary is generated, extract durable facts:

```
python -c "
from pathlib import Path
from scripts.fact_extractor import extract_facts_from_summary, persist_facts
import glob

summaries = sorted(glob.glob('tmp/session_history_*.json'))
if summaries:
    facts = extract_facts_from_summary(Path(summaries[-1]), Path('.'))
    count = persist_facts(facts, Path('.'))
    print(f'Extracted {count} new facts to state/memory.md')
else:
    print('No session summary found — skipping fact extraction')
"
```

Additionally, if during THIS session the user corrected a previous
finding (e.g., "that's not an anomaly" or "ignore X"), create an
explicit correction fact:

```markdown
- id: correction-{domain}-{slug}
  type: correction
  domain: {domain}
  content: "{user's correction}"
  source: "catch-up {date}, user correction"
  confidence: 1.0
  ttl_days: null
```
```

3. **state/memory.md schema update:**

Update `state/templates/memory.md` with the facts schema:

```markdown
---
domain: memory
last_updated: never
schema_version: '2.0'
facts: []
---

## Memory & Learned Facts

This file stores durable facts extracted from catch-up sessions.
Do NOT manually edit the `facts:` frontmatter — it is managed by
`scripts/fact_extractor.py`.

### User Corrections
<!-- corrections added automatically from catch-up sessions -->

### Observed Patterns
<!-- patterns detected across 3+ sessions -->

### Preferences
<!-- user behavior and preference signals -->
```

4. **Integration with Step 8 OODA (Phase 1):**

Add to the OODA OBSERVE phase in `config/workflow/reason.md`:

```markdown
### 8-O: OBSERVE — Gather Evidence

[... existing content ...]

Additionally, read `state/memory.md` facts section. For each fact:
- **Corrections:** Suppress any alert that matches a correction fact
- **Patterns:** Reference known patterns when evaluating anomalies
- **Thresholds:** Use user-calibrated thresholds instead of defaults
```

**Files changed:**

| File | Change | Risk |
|------|--------|:----:|
| `scripts/fact_extractor.py` | **Create** (~250 lines) | Med |
| `state/templates/memory.md` | Update schema to v2.0 with facts | Low |
| `config/workflow/finalize.md` | Add Step 11c | Low |
| `config/workflow/reason.md` | OODA OBSERVE references memory.md facts | Low |
| `config/artha_config.yaml` | Add `harness.agentic.fact_extraction.enabled: true` | Low |
| `tests/unit/test_fact_extractor.py` | **Create** | — |

**Tests:**
- `test_extract_correction_fact`: User correction detected in session summary
- `test_extract_pattern_fact`: Recurring observation detected
- `test_dedup_by_id`: Same-ID facts not duplicated
- `test_dedup_updates_confidence`: Higher confidence replaces lower
- `test_expire_stale_facts`: ttl_days-expired facts removed during persist
- `test_persist_rebuilds_frontmatter`: New facts appended; body preserved
- `test_empty_summary_returns_empty`: No summary → no facts extracted
- `test_schema_v2_backward_compat`: Old memory.md without facts section still loads
- `test_feature_flag_disabled_skips`: When flag false, no extraction runs

**Rollback:** Set `harness.agentic.fact_extraction.enabled: false`. Remove
Step 11c from finalize.md. Remove OBSERVE memory.md reference from reason.md.
Delete `fact_extractor.py`. `state/memory.md` remains but is ignored.

**Exit criteria:**
- `fact_extractor.py` passes all unit tests
- `state/templates/memory.md` has v2.0 schema with `facts: []`
- After 3 catch-ups, `state/memory.md` has ≥5 extracted facts
- User corrections from session N are not re-triggered in session N+1
- OODA OBSERVE phase references memory.md facts
- Existing session_summarizer.py unchanged

**Risks:**

| Risk | L | I | Mitigation |
|------|:-:|:-:|------------|
| Fact extraction hallucination | M | M | Facts extracted from structured session summary JSON, not free-text. Confidence < 0.8 for inferred facts. Corrections from explicit user input always confidence=1.0. |
| memory.md grows unbounded | L | L | TTL-based expiry. Patterns expire at 180d, schedules at 90d. Corrections and preferences indefinite but low-volume. Cap at 500 facts (oldest evicted). |
| Write guard blocks memory.md update | L | M | fact_extractor appends to facts frontmatter. Net-negative check: frontmatter GROWS (more facts). Body unchanged. Guard should pass. |
| PII in extracted facts | M | H | facts go through PII middleware before write. Contact facts use `[REDACTED]` for sensitive fields. fact_extractor strips phone numbers, emails, SSNs. |

---

### Phase 6: Sub-Agent Context Isolation with Handoff Compression (FUTURE)

> **Status: Deferred to P3.** Depends on IDE sub-agent API stabilization
> across Claude Code, Copilot, and Gemini CLI.

**Goal:** Process each domain in a separate sub-agent context window.
The orchestrator passes only domain-relevant context (state file + prompt +
routed emails). Sub-agent returns structured output (1–5 bullets + alerts +
state delta). Orchestrator never sees raw extraction context.

**Inspired by:**
- **OpenAI Agents SDK** `input_filter` — before handing off, compress
  conversation history into a domain-specific summary. The delegate agent
  receives only what it needs.
- **AutoGen** `RoutedAgent` — agents subscribe to topics. The runtime
  routes messages by topic, not by explicit delegation.
- **OpenAI SDK** `Agent.as_tool()` — treat a sub-agent as a sandboxed
  tool call with its own context window.

**Key design decisions:**

1. **Handoff compression (from OpenAI SDK):** Before delegating to a
   domain sub-agent, the orchestrator generates a **handoff summary**:
   - Current date, user identity, command context
   - This domain's state file frontmatter (not full body)
   - Routed emails for this domain (subject + sender, not full body)
   - Active alerts from preflight
   - Relevant corrections from `state/memory.md` facts

   This replaces the full conversation history. Sub-agent starts fresh
   with ~2K tokens of context instead of inheriting the orchestrator's
   30K–100K token conversation.

2. **Structured return contract:** Each sub-agent returns:
   ```json
   {
     "domain": "finance",
     "briefing_bullets": ["..."],
     "alerts": [{"severity": "urgent", "description": "..."}],
     "state_delta": {"field": "new_value"},
     "facts_extracted": [{"type": "pattern", "content": "..."}]
   }
   ```

3. **IDE API abstraction:** Wrap the IDE-specific sub-agent API behind
   a `SubAgentRunner` protocol. Initial implementation: VS Code
   `runSubagent`. Future: Claude Code native sub-agent, Gemini CLI
   sub-agent.

**Pre-conditions for starting Phase 6:**
- Phases 1–5 complete and stable (4+ weeks production)
- IDE sub-agent API stable for 2+ releases
- ArthaContext (Phase 3) provides the handoff data
- Persistent facts (Phase 5) provides the correction context for handoffs
- Cross-CLI compatibility tested

**Interface sketch (for future reference):**

```python
class SubAgentRunner(Protocol):
    """Protocol for IDE-specific sub-agent invocation."""

    def delegate(
        self,
        agent_name: str,
        handoff_summary: str,
        prompt: str,
        *,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Invoke a sub-agent with compressed context.

        Returns structured output matching DomainResult schema.
        """
        ...


class VSCodeSubAgentRunner:
    """VS Code runSubagent implementation."""

    def delegate(self, agent_name, handoff_summary, prompt, *, max_tokens=4096):
        # Uses VS Code's runSubagent tool
        ...


class PromptSimulatedRunner:
    """Fallback: simulates isolation via prompt boundaries."""

    def delegate(self, agent_name, handoff_summary, prompt, *, max_tokens=4096):
        # Uses prompt markers: "--- BEGIN ISOLATED DOMAIN CONTEXT ---"
        # No real isolation, but structured handoff still reduces context
        ...
```

**Token savings estimate:** With 5 active domains, each processed in
isolation with ~2K handoff → ~10K total. Current shared context: ~40K
for Step 7. Savings: ~30K tokens, freeing capacity for deeper reasoning
in Step 8.

---

## 7. Risk Register

### Phase-Specific Risks

| ID | Risk | Phase | L | I | Mitigation |
|----|------|:-----:|:-:|:-:|------------|
| R1 | AI ignores OODA protocol entirely | 1 | M | M | ⛩️ gate in reason.md. audit_compliance.py checks for `[OBSERVE]`, `[ORIENT]`, `[DECIDE]`, `[ACT]` markers. |
| R2 | OODA adds tokens to already-constrained Step 8 | 1 | L | L | Protocol is ~800 tokens. OODA replaces existing ad-hoc reasoning, not additive. |
| R3 | Wrong artifact assigned to wrong eviction tier | 2 | L | M | `_ARTIFACT_TIERS` is code-reviewed. Golden-file tests verify tier assignments. |
| R4 | CRITICAL artifacts retained leave insufficient room | 2 | L | L | CRITICAL artifacts are small (~600 tokens combined). EPHEMERAL eviction frees 20K+ tokens. Net positive. |
| R5 | ArthaContext becomes a god-object | 3 | M | M | Strict SRP: context CARRIES state, doesn't DECIDE. Decision logic stays in middleware/prompts. |
| R6 | Checkpoint file creates inconsistent state | 4 | M | M | 4-hour TTL. User confirmation before resume. Auto-resume only in automated/pipeline mode. |
| R7 | Stale checkpoint resumes with outdated pipeline data | 4 | L | H | Pipeline JSONL has timestamps. Session summarizer has timestamps. AI validates data freshness before using. |
| R8 | Fact extraction hallucinates facts | 5 | M | M | Facts come from structured session summary JSON, not free-text inference. Inferred facts get confidence=0.8, not 1.0. |
| R9 | memory.md grows unbounded | 5 | L | L | TTL expiry. 500-fact cap. EPHEMERAL patterns expire at 180d. |
| R10 | PII leaks through extracted facts | 5 | M | H | PII middleware runs before memory.md write. Contact facts redact sensitive fields. fact_extractor strips known PII patterns. |

### Cross-Phase Risks

| ID | Risk | Phases | L | I | Mitigation |
|----|------|:------:|:-:|:-:|------------|
| R11 | Feature flag proliferation under `harness.agentic:` | All | M | L | Max 6 flags (one per phase). Grouped under single namespace. Each defaults to `true` after stabilization. |
| R12 | New modules increase maintenance surface | All | M | M | Each module has SRP responsibility. ~50–250 lines each. Comprehensive unit tests. No cross-module coupling except ArthaContext (Phase 3). |
| R13 | Phases interact unexpectedly | All | L | M | Integration test validates all phases enabled simultaneously. Each phase has independent rollback. |
| R14 | Backward compat with DA-1–5 and VM-0–5 | All | L | H | All changes additive. Existing feature flags unchanged. New flags under `harness.agentic:` namespace (separate from `harness:`). |

---

## 8. Trade-Off Decision Log

### TD-1: Prompt-based OODA vs. Python OODA orchestrator

**Chosen: Prompt-based OODA protocol (Option A).**
Artha's core philosophy (TS §1.1): "Prompts are the logic layer." Step 8
cross-domain reasoning is a reasoning task — exactly where prompts excel.
A Python orchestrator would force Step 8 into deterministic code, losing
the AI's ability to discover unexpected compound signals. The OODA protocol
gives structure without removing flexibility.
**Trade-off accepted:** Compliance is probabilistic (~75%). Mitigated by
⛩️ gate enforcement + post-hoc audit_compliance.py check.
**Reversibility:** Revert reason.md content. Zero code impact.

### TD-2: Fixed eviction tiers vs. dynamic adaptive priority

**Chosen: Fixed priority constants (Option A).**
The artifact set is well-known and stable (pipeline JSONL, domain
extractions, alerts, ONE THING). Dynamic adaptation adds a feedback loop
(context pressure → priority adjustment → different offloading → different
pressure). Feedback loops in an already-complex prompt-driven system create
unpredictable behavior. Fixed tiers are deterministic and testable.
**Trade-off accepted:** If a new artifact type is added, the developer
must manually assign its tier in `_ARTIFACT_TIERS`. This is a feature,
not a bug — it forces conscious tier assignment.
**Reversibility:** Set feature flag to false → flat threshold.

### TD-3: ArthaContext as Pydantic model vs. TypedDict vs. prompt-only

**Chosen: Pydantic model (Option A).**
Pydantic already a dependency. Runtime validation catches integration
errors that TypedDict's static-only checking would miss. The model's
`.model_dump()` produces clean YAML for health-check.md serialization.
**Trade-off accepted:** Pydantic models are heavier than dataclasses.
Acceptable — ArthaContext is constructed once per session, not per-step.
**Reversibility:** Delete `artha_context.py`. Remove `ctx` from
middleware Protocol.

### TD-4: Explicit checkpoint file vs. passive artifact existence

**Chosen: Explicit checkpoint file (Option B).**
Passive artifact checks ("if `tmp/pipeline_output.jsonl` exists, skip Step 4")
are unreliable — the AI may not check. An explicit `tmp/.checkpoint.json`
with `last_step` field gives deterministic resumption. The AI reads ONE file
instead of checking N artifact locations.
**Trade-off accepted:** Adds a write operation after each major step
(~5ms each, ~7 steps = ~35ms total). Negligible.
**Reversibility:** Delete `checkpoint.py`. Remove checkpoint instructions
from workflow files.

### TD-5: Script-based fact extraction vs. inline LLM extraction

**Chosen: Script-based extraction (Option A).**
Post-session extraction from structured JSON is higher quality than
in-flight LLM inference during Step 8. Inline extraction (Option B)
would interleave reasoning with storage, violating SRP. The script reads
the already-generated `tmp/session_history_{N}.json`, which has
structured fields (`key_findings`, `state_mutations`, `open_threads`).
**Trade-off accepted:** Extraction runs after the session, not during.
Facts from the current session are not available to the current session's
reasoning. This is by design — facts need validation before they
influence future reasoning.
**Reversibility:** Set flag false. Delete `fact_extractor.py`.

### TD-6: Sub-agent isolation deferred vs. immediate prompt simulation

**Chosen: Defer to P3.**
Real sub-agent isolation requires IDE API stability. VS Code's
`runSubagent` works in Copilot but the API surface may change.
Claude Code and Gemini CLI have different sub-agent mechanisms.
Prompt-simulated isolation (Option B) provides no actual context
savings — just organizational structure. The real value is separate
context windows. Wait for API maturity.
**Trade-off accepted:** Step 7 continues to process all domains in
shared context. DA-1 context offloading and DA-2 progressive disclosure
already reduce this burden significantly.
**Reversibility:** N/A (deferred, not implemented).

---

## 9. Assumptions & Constraints

### Assumptions

| # | Assumption | If Wrong | Validation |
|---|-----------|----------|------------|
| A1 | OODA protocol markers are reliably generated by AI at ~75%+ | Compliance too low → add schema validation (Option C) | Measure over 10 catch-ups via audit_compliance.py |
| A2 | CRITICAL artifacts (alerts, ONE THING) total <1K tokens | Keeping them in-context wastes space | Measure via harness_metrics eviction_tiers |
| A3 | `state/memory.md` will contain <500 facts after 1 year | Unbounded growth | Monitor monthly; adjust TTLs if needed |
| A4 | Session summary JSON has sufficient structure for fact extraction | Extraction quality too low | Review first 10 extraction runs manually |
| A5 | 4-hour checkpoint TTL is correct | Too short: fresh session needed sooner. Too long: stale data used | Adjust _MAX_AGE_HOURS based on user patterns |
| A6 | Pydantic already in requirements (DA-5 dependency) | Import adds latency | Verified: `pydantic>=2.0.0` in scripts/requirements.txt |
| A7 | IDE sub-agent APIs will stabilize within 6 months | Phase 6 indefinitely deferred | Monitor VS Code/Claude Code release notes quarterly |

### Hard Constraints

| # | Constraint | Source | Design Impact |
|---|-----------|--------|---------------|
| C1 | Artha must remain prompt-driven | TS §1.1 | Phase 1 is prompt-only. Phases 2–5 are scripts that inform prompts. No Python orchestrator. |
| C2 | Zero-downtime migration | TS §1.1, operational | Strangler Fig. Feature flags. Per-phase rollback. |
| C3 | Context window ~200K tokens | LLM architecture | OODA must not inflate Step 8 significantly. Tiered eviction preserves critical context. |
| C4 | All new config under `harness.agentic:` namespace | Convention (DA-1–5 uses `harness:`) | No naming collisions with existing config. |
| C5 | `state/memory.md` uses YAML frontmatter + Markdown body | State file convention | Fact schema must fit in frontmatter. Middleware guards apply. |
| C6 | `tmp/` is ephemeral | DA-1 design | Checkpoints in `tmp/` are session-scoped. Facts persist to `state/`. |

---

## 10. Success Criteria

### Quantitative Metrics (measured via harness_metrics + audit_compliance)

| Metric | Baseline | Phase 1 | Phase 1–2 | Phase 1–5 |
|--------|:--------:|:-------:|:---------:|:---------:|
| `compound_signals_fired` (avg, heavy day) | 1.2 | **2.5+** | 2.5+ | 3.0+ |
| OODA compliance rate | 0% | **≥70%** | ≥70% | ≥75% |
| Critical context evicted under normal pressure | Yes | Yes | **Never** | Never |
| Checkpoint-based recovery success rate | N/A | N/A | N/A | **≥80%** |
| New facts per catch-up (avg) | 0 | 0 | 0 | **2–5** |
| Repeated false-positive alerts (same correction needed 2x) | ~30% | ~30% | ~30% | **<5%** |
| Context pressure `critical` rate | <5% (post DA-1) | <5% | **<3%** | <2% |
| health-check `session:` metrics present | No | No | No | **Yes** |
| Test count delta | +0 | +15 | +30 | +60 |

### Qualitative Criteria

| Criterion | Verification | Phase |
|-----------|-------------|:-----:|
| OODA markers visible in cross-domain analysis output | Inspect 5 catch-up tmp/cross_domain_analysis.json files | 1 |
| Alerts and ONE THING never offloaded at green/yellow/red pressure | Check offloader logs across 10 catch-ups | 2 |
| ArthaContext serialized in health-check.md harness_metrics | Inspect 3 catch-ups | 3 |
| Checkpoint file created after Steps 4, 7, 8 | Check tmp/.checkpoint.json during catch-up | 4 |
| memory.md facts grow across sessions | Check after 5 catch-ups | 5 |
| User corrections not repeated in subsequent sessions | Test with deliberate correction | 5 |
| All feature flags independently toggleable | Disable each, verify no cascade | All |

---

## 11. Dependency Map & Critical Path

```
Phase 1 (OODA Protocol)         Phase 2 (Tiered Eviction)
     │  Zero code — prompt only       │  ~50 lines
     │                                │
     │  PARALLEL (no dependency)      │
     │                                │
     └──────────┬─────────────────────┘
                │
                ▼
     Phase 3 (ArthaContext)
        ~200 lines. Middleware integration.
        Benefits from Phase 2 (pressure level informs eviction).
                │
                ▼
     Phase 4 (Checkpoints)
        ~80 lines. Workflow file updates.
        Uses ArthaContext for step tracking.
                │
                ▼
     Phase 5 (Persistent Facts)
        ~250 lines + schema.
        Reads session summaries (DA-3).
        Memory.md facts used by OODA (Phase 1).
        Closes the learning loop.
                │
                ▼ (future)
     Phase 6 (Sub-Agent Isolation)
        Requires Phases 1–5 + IDE API stability.
        Uses ArthaContext (Phase 3) for handoff data.
        Uses Persistent Facts (Phase 5) for correction context.
```

### Critical Path

Phase 1 + Phase 2 (parallel) → Phase 3 → Phase 4 → Phase 5

### External Dependencies

| Dependency | Required By | Risk |
|-----------|-------------|------|
| `pydantic` | Phase 3, 5 | Already in requirements.txt (DA-5) |
| `PyYAML` | Phase 5 | Already in requirements.txt |
| VS Code sub-agent API | Phase 6 | External, unstable — deferred |

### Internal Dependencies

| Module | Depends On | Depended On By |
|--------|-----------|---------------|
| reason.md OODA content | None | Phase 5 (OBSERVE reads memory.md facts) |
| `context_offloader.py` (enhanced) | None | Phase 3 (ArthaContext informs pressure) |
| `artha_context.py` | `detect_environment.py`, `preflight.py` | Phase 4 (step tracking), Phase 5 (session metadata) |
| `checkpoint.py` | `artha_context.py` (optional) | None (self-contained) |
| `fact_extractor.py` | `session_summarizer.py` (DA-3) | Phase 6 (correction context in handoffs) |

---

## 12. Backward Compatibility Contract

### Invariants (NEVER broken)

| # | Invariant | Enforcement |
|---|-----------|-------------|
| 1 | Existing `harness:` config namespace unchanged | New flags under `harness.agentic:` |
| 2 | `context_offloader.py` flat-threshold behavior when tiered_eviction disabled | Feature flag test |
| 3 | `middleware/` Protocol backward compatible (`ctx=None` default) | Protocol test |
| 4 | `state/memory.md` readable by current scripts when facts section absent | Schema v1 → v2 compatibility test |
| 5 | All existing DA-1–5 and VM-0–5 tests pass at every phase | CI gate |
| 6 | Workflow files remain readable without OODA/checkpoint content | Content is additive, not replacing |
| 7 | `audit_compliance.py` existing checks unchanged | New checks additive |
| 8 | `tmp/` cleanup at Step 18 covers all new artifacts | OFFLOADED_FILES manifest updated |

### Per-Phase Rollback

| Phase | Rollback |
|-------|----------|
| 1 | Revert reason.md Step 8 content. Remove OODA compliance check. |
| 2 | Set `tiered_eviction.enabled: false`. Flat threshold restored. |
| 3 | Delete `artha_context.py`. Remove `ctx` from middleware Protocol. |
| 4 | Delete `checkpoint.py`. Remove checkpoint instructions from workflow files. Clear `tmp/.checkpoint.json`. |
| 5 | Delete `fact_extractor.py`. Revert memory.md template. Remove Step 11c from finalize.md. Remove OBSERVE memory.md reference from reason.md. |
| 6 | N/A (deferred) |

---

## 13. Observability Strategy

### Telemetry Points

| Phase | Metric | Type | Destination |
|-------|--------|------|-------------|
| 1 | `ooda.compliance_rate` | Gauge (%) | audit_compliance.py output |
| 1 | `ooda.compound_signals_fired` | Counter | harness_metrics |
| 1 | `ooda.cycles_executed` | Counter (0–2) | tmp/cross_domain_analysis.json |
| 2 | `eviction.tier_counts` | Counter per tier | harness_metrics |
| 2 | `eviction.critical_preserved_tokens` | Gauge | harness_metrics |
| 3 | `session.command` | Label | harness_metrics |
| 3 | `session.environment` | Label | harness_metrics |
| 3 | `session.pressure` | Label | harness_metrics |
| 3 | `session.steps_executed` | List | harness_metrics |
| 4 | `checkpoint.last_step` | Gauge | tmp/.checkpoint.json |
| 4 | `checkpoint.resume_count` | Counter | harness_metrics |
| 5 | `facts.extracted_count` | Counter | harness_metrics |
| 5 | `facts.total_count` | Gauge | state/memory.md frontmatter |
| 5 | `facts.corrections_applied` | Counter | harness_metrics |
| 5 | `facts.expired_count` | Counter | harness_metrics |

### eval_runner.py Integration

```bash
# Check OODA compliance across last 10 briefings
python scripts/eval_runner.py --compliance --last 10 --check ooda_protocol

# View fact extraction trends
python scripts/eval_runner.py --facts --last 30

# Checkpoint recovery rate
python scripts/eval_runner.py --checkpoints --last 10
```

---

## 14. Security Analysis

### Threat Model — Changes Introduced

| Threat | Phase | OWASP | Mitigation |
|--------|:-----:|-------|------------|
| Fact extraction stores PII in memory.md | 5 | Cryptographic Failures | PII middleware runs before memory.md write. Contact facts use `[REDACTED]`. fact_extractor strips known PII patterns (phone, email, SSN regex). |
| Checkpoint file reveals session state | 4 | Security Misconfiguration | Checkpoint is in `tmp/` (ephemeral). Contains step numbers and metadata, not sensitive data. Cleaned at Step 18. |
| ArthaContext serialization leaks to health-check | 3 | Information Disclosure | Serialized subset only: command, environment, pressure, step counts. No credentials, no PII, no email content. |
| Malicious fact injection via memory.md | 5 | Injection | memory.md writes go through middleware stack (PII + write guard + write verify). Schema validation rejects malformed facts. |
| OODA protocol output contains unredacted data | 1 | Information Disclosure | OODA output goes to tmp/cross_domain_analysis.json (DA-1 offloading). PII middleware applies to offloaded content. |

### Unchanged Security Properties

| Asset | Protection | Changed? |
|-------|-----------|:--------:|
| State files (.age) | age encryption | No |
| OAuth tokens | File perms 0o600, keyring | No |
| PII in state files | PII middleware (DA-4) | No |
| Briefings | Plaintext (PII filtered) | No |
| tmp/ artifacts | Cleaned at Step 18 | No (checkpoint added to cleanup) |

---

## Appendix A: Complete File Change Manifest

| Phase | File | Action | Est. Lines | Risk |
|-------|------|--------|:----------:|:----:|
| **1** | `config/workflow/reason.md` | Modify (+OODA protocol) | +120 | Med |
| **1** | `scripts/audit_compliance.py` | Modify (+OODA check) | +20 | Low |
| **1** | `tests/unit/test_audit_compliance.py` | Modify (+OODA tests) | +30 | — |
| **2** | `scripts/context_offloader.py` | Modify (+EvictionTier, +_ARTIFACT_TIERS) | +50 | Low |
| **2** | `config/Artha.core.md` | Modify (+eviction_tiers in harness_metrics) | +10 | Low |
| **2** | `config/artha_config.yaml` | Modify (+harness.agentic.tiered_eviction) | +3 | Low |
| **2** | `tests/unit/test_context_offloader.py` | Modify (+tier tests) | +50 | — |
| **3** | `scripts/artha_context.py` | **Create** | ~200 | Low |
| **3** | `scripts/middleware/__init__.py` | Modify (+ctx parameter) | +5 | Low |
| **3** | `config/Artha.core.md` | Modify (+session metrics in harness_metrics) | +15 | Low |
| **3** | `config/artha_config.yaml` | Modify (+harness.agentic.context) | +3 | Low |
| **3** | `tests/unit/test_artha_context.py` | **Create** | ~80 | — |
| **4** | `scripts/checkpoint.py` | **Create** | ~80 | Low |
| **4** | `config/workflow/preflight.md` | Modify (+Step 0a checkpoint check) | +15 | Low |
| **4** | `config/workflow/fetch.md` | Modify (+checkpoint write) | +5 | Low |
| **4** | `config/workflow/process.md` | Modify (+checkpoint write) | +5 | Low |
| **4** | `config/workflow/reason.md` | Modify (+checkpoint write) | +5 | Low |
| **4** | `config/workflow/finalize.md` | Modify (+checkpoint clear) | +5 | Low |
| **4** | `config/artha_config.yaml` | Modify (+harness.agentic.checkpoints) | +3 | Low |
| **4** | `scripts/context_offloader.py` | Modify (+.checkpoint.json to cleanup) | +2 | Low |
| **4** | `tests/unit/test_checkpoint.py` | **Create** | ~60 | — |
| **5** | `scripts/fact_extractor.py` | **Create** | ~250 | Med |
| **5** | `state/templates/memory.md` | Modify (+v2.0 facts schema) | +15 | Low |
| **5** | `config/workflow/finalize.md` | Modify (+Step 11c) | +20 | Low |
| **5** | `config/workflow/reason.md` | Modify (+OBSERVE memory.md reference) | +10 | Low |
| **5** | `config/artha_config.yaml` | Modify (+harness.agentic.fact_extraction) | +3 | Low |
| **5** | `tests/unit/test_fact_extractor.py` | **Create** | ~100 | — |

**Total: 4 new files, 16 modified, 0 deleted.**
**Estimated net new code: ~580 lines production + ~320 lines tests = ~900 lines.**

---

## Appendix B: Cross-Reference with Predecessor Specs

### Relationship to specs/deep-agents.md

| This Plan | Deep Agents | Interaction |
|-----------|-------------|-------------|
| Phase 1: OODA | N/A | New capability — addresses reasoning quality gap not covered in DA |
| Phase 2: Tiered Eviction | Phase 1 (context offloading) | **Enhancement** — adds priority dimensions to existing offloader |
| Phase 3: ArthaContext | Phase 6 (State Abstraction, DEFERRED) | **Different scope** — ArthaContext is runtime context, not storage abstraction. Phase 6 remains deferred |
| Phase 4: Checkpoints | N/A | New capability — leverages DA-1 tmp/ artifacts as implicit checkpoints |
| Phase 5: Persistent Facts | Phase 3 (Session Summarization) | **Extension** — reads session summaries (DA-3 output) and persists facts long-term |
| Phase 6: Sub-Agent Isolation | Phase 7 (Sub-Task Isolation, EXPERIMENTAL) | **Supersedes** — adds handoff compression (OpenAI SDK pattern) to DA-7's conceptual model |

### Relationship to specs/vm-hardening.md

| This Plan | VM Hardening | Interaction |
|-----------|-------------|-------------|
| Phase 1: OODA | Phase 4: Compliance Gates | Complementary — OODA protocol goes into workflow files that already have ⛩️ gates |
| Phase 2: Tiered Eviction | N/A | No interaction |
| Phase 3: ArthaContext | Phase 1: detect_environment | **Consumes** — ArthaContext.environment populated from EnvironmentManifest |
| Phase 3: ArthaContext | Phase 2: Preflight hardening | **Consumes** — ArthaContext.preflight_passed from CheckResult list |
| Phase 4: Checkpoints | Phase 4: Read-Only skip list | Compatible — checkpoints in tmp/ (writable even in VM). Read-only mode skips write steps but checkpoints still track which steps ran |
| Phase 5: Persistent Facts | Phase 4: Silent Failure Block | Compatible — facts don't interact with connector health |

### Status of Deferred DA Phases

| Phase | Original Status | New Status |
|-------|----------------|------------|
| Phase 6: State Abstraction | DEFERRED | **Still deferred** — ArthaContext (Phase 3 here) addresses runtime context but NOT storage abstraction. Phase 6 revisited when storage needs expand (mobile access, cloud sync). |
| Phase 7: Sub-Task Isolation | EXPERIMENTAL | **Superseded by this plan's Phase 6** — adds handoff compression and structured return contracts. Still gated on IDE API stability. |

---

## Appendix C: Framework Derivation Traceability

Each primitive traces to specific framework features studied:

| Primitive | Framework Feature | What We Adapted | What We Rejected |
|-----------|------------------|-----------------|-------------------|
| **FP-1 OODA** | SK `KernelFunction` loop: Plan → Execute → Evaluate | 4-phase protocol with named output blocks | Planner auto-selection, function composition API |
| **FP-1 OODA** | LangGraph `StateGraph` cycles | Cycle-back from ACT to ORIENT on validation failure | Graph compiler, node execution engine, persistence layer |
| **FP-2 Tiers** | Agno `AgentMemory(session_memory, run_memory, user_memory)` | 4-level eviction priority constants | AgentMemory class hierarchy, database storage, embedding retrieval |
| **FP-3 Context** | Pydantic AI `RunContextWrapper[T]` | Typed Pydantic model injected into middleware | Agent framework, LLM tool binding, dependency injection container |
| **FP-3 Context** | OpenAI SDK `RunContextWrapper` | Code-side context separate from LLM context | SDK agent loop, tool registration protocol |
| **FP-4 Checkpoints** | LangGraph `StateGraph.add_checkpoint()` | Marker file after major steps; artifact-existence implies completion | PostgreSQL checkpoint storage, thread isolation, graph replay |
| **FP-5 Facts** | Agno `UserMemory` persistent facts | Post-session extraction to state/memory.md with structured schema | Embedding-based similarity search, vector store, classification ML |
| **FP-5 Facts** | SK `SemanticMemory` key-value facts | Fact ID deduplication, TTL expiry | Embedding memory, semantic search, kernel integration |
| **FP-6 Handoff** | OpenAI SDK `input_filter` on handoffs | Compress conversation into domain summary before delegation | Handoff routing, agent swarms, SDK conversation manager |
| **FP-6 Handoff** | AutoGen `RoutedAgent` pub/sub | Topic-based routing to domain sub-agents | MQTT-style runtime, message type subscriptions, distributed execution |
| **FP-6 Handoff** | OpenAI SDK `Agent.as_tool()` | Sandboxed sub-computation with structured return | Tool registration framework, concurrent agent execution |

---

## Appendix D: Configuration Schema

All new configuration lives under `harness.agentic:` in
`config/artha_config.yaml`, separate from the existing `harness:` namespace:

```yaml
harness:
  # ... existing DA-1–5 config unchanged ...

  agentic:
    # Phase 2: Tiered eviction priority
    tiered_eviction:
      enabled: true

    # Phase 3: ArthaContext typed runtime context
    context:
      enabled: true

    # Phase 4: Step checkpoints
    checkpoints:
      enabled: true
      max_age_hours: 4

    # Phase 5: Persistent fact extraction
    fact_extraction:
      enabled: true
      max_facts: 500
      default_ttl_days: null  # null = indefinite for corrections/preferences
```

Note: Phase 1 (OODA) has no config flag — it's prompt content, always
active. To disable: revert reason.md Step 8 content.
