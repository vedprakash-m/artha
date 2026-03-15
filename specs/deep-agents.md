# Artha — Deep Agents Architecture Adoption Plan

> **Version**: 1.0 | **Status**: Proposed | **Date**: March 15, 2026
> **Author**: [Author] | **Classification**: Internal — Architecture
> **Implements**: Lessons from [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) harness architecture
> **Supersedes**: None (new initiative)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [AS-IS Analysis](#2-as-is-analysis)
3. [Pain Points](#3-pain-points)
4. [Design Proposals & Trade-off Matrix](#4-design-proposals--trade-off-matrix)
5. [Execution Plan — Strangler Fig Migration](#5-execution-plan--strangler-fig-migration)
   - [Phase 1: Context Offloading](#phase-1-context-offloading-strangler-layer-1)
   - [Phase 2: Progressive Domain Disclosure](#phase-2-progressive-domain-disclosure-strangler-layer-2)
   - [Phase 3: Session Summarization](#phase-3-session-summarization-strangler-layer-3)
   - [Phase 4: Middleware Formalization](#phase-4-middleware-formalization-strangler-layer-4)
   - [Phase 5: Structured Output Validation](#phase-5-structured-output-validation-strangler-layer-5)
   - [Phase 6: State Abstraction Layer](#phase-6-state-abstraction-layer-strangler-layer-6)
   - [Phase 7: Sub-Task Context Isolation](#phase-7-sub-task-context-isolation-strangler-layer-7)
6. [Risk Register](#6-risk-register)
7. [Success Criteria](#7-success-criteria)
8. [Dependency Map](#8-dependency-map)
9. [Backward Compatibility Contract](#9-backward-compatibility-contract)
10. [Observability Strategy](#10-observability-strategy)

---

## 1. Executive Summary

### What is Deep Agents?

Deep Agents (langchain-ai/deepagents) is an open-source "agent harness" — an opinionated, batteries-included framework for building long-running LLM agents. Inspired by Claude Code, it ships four core capabilities: **planning tools**, **virtual filesystem with pluggable backends**, **sub-agent spawning with context isolation**, and **automatic context management** (offloading + summarization).

### Why this matters for Artha

Artha is a prompt-driven personal intelligence system. The LLM instruction file (Artha.md) IS the application. This architecture works, but it faces a structural ceiling: **the 200K context window is both the runtime AND the constraint.** As Artha scales to 20+ domains, 200+ emails per catch-up, and multi-command sessions, context pressure is the single biggest bottleneck to reliability and output quality.

Deep Agents solves this class of problem systematically. This spec adapts seven of its patterns to Artha's unique architecture (Markdown state, prompt-as-logic, zero-daemon model) while preserving the design philosophy that makes Artha work.

### What this is NOT

- **Not a rewrite.** Artha stays prompt-driven, Markdown-based, and zero-daemon.
- **Not a dependency.** We do not import or depend on the `deepagents` Python library.
- **Not an abstraction exercise.** Each phase ships a concrete, testable improvement.

### Guiding Principles

| Principle | Application |
|-----------|-------------|
| **Strangler Fig** | Each phase wraps existing behavior, never replaces it atomically. Old path remains functional behind a feature flag until the new path proves stable. |
| **SOLID / SRP** | Each new module does exactly one thing. `context_offloader.py` offloads. `session_summarizer.py` summarizes. No god-objects. |
| **Twelve-Factor** | Config in `config/`, state in `state/`, ephemeral in `tmp/`. No environment-dependent behavior changes without explicit configuration. |
| **Backward Compatibility** | No catch-up workflow step changes its output contract. All new behavior is additive or opt-in via `artha_config.yaml` flags. |

---

## 2. AS-IS Analysis

### Current Architecture Topology

```
User → AI CLI → Artha.md (instruction file) → 21-step catch-up workflow
                    │
                    ├─ prompts/*.md        (domain logic — 18 domains)
                    ├─ state/*.md(.age)    (world model — 31 files)
                    ├─ scripts/pipeline.py (connector orchestrator — 8 connectors)
                    ├─ scripts/skill_runner.py (data skills — 9 skills)
                    ├─ scripts/vault.py    (encryption — age + keyring)
                    └─ scripts/pii_guard.py (PII redaction)
```

### Context Flow — Current State

```
Step 0:  preflight.py          → ~500 tokens (health-check, gate result)
Step 1:  vault decrypt         → 0 tokens (side-effect only)
Step 2:  health-check read     → ~2K tokens
Step 4:  pipeline.py JSONL     → 5K–80K tokens (depends on email volume)
Step 4b: tiered state loading  → 10K–40K tokens (6 always + N active domains)
Step 5:  PII + email preproc   → rebased within Step 4 output
Step 6:  domain routing        → ~1K tokens (routing table in context)
Step 7:  domain processing     → 20K–60K tokens (prompt files + extraction logic)
Step 8:  cross-domain reason   → 5K–15K tokens (scoring, guards, reasoning)
Step 11: briefing synthesis    → 3K–8K tokens (output assembly)

Total context at peak (Step 8): 50K–200K tokens
```

**The problem is clear:** On heavy email days (200+ emails), Steps 4–8 consume 80–90% of the context window. Cross-domain reasoning (Step 8) operates on degraded context. The AI must hold raw email data, domain prompts, state files, routing tables, AND reasoning output simultaneously. This is a single-threaded, monolithic context.

### What works well (preserve these)

| Capability | Why it works |
|------------|-------------|
| Tiered state loading (Step 4b) | Already reduces context by 40–60% for dormant domains |
| Volume tiers (Step 4d) | Email processing adapts to batch size (standard/medium/high/extreme) |
| Context pressure tracking (Step 16) | Health-check already logs green/yellow/red/critical pressure |
| Parallel pipeline execution | ThreadPoolExecutor in pipeline.py + skill_runner.py |
| Net-negative write guard | Prevents data loss from degraded-context writes |
| Ephemeral cleanup (Step 18) | Corporate data deletion, re-encryption |

### What Deep Agents does better

| Deep Agents Pattern | Artha Gap | Impact |
|---------------------|-----------|--------|
| **Automatic context offloading** — tool results >20K tokens → written to file, replaced with path + 10-line preview | Pipeline JSONL output sits in context through Steps 4–8 even after routing is complete | High — frees 20K–60K tokens |
| **Progressive skill disclosure** — only frontmatter loaded at startup; full content loaded on-demand | All 6 Tier A domain prompts loaded unconditionally; `/status` loads immigration prompt even with no immigration activity | High — frees 5K–15K tokens for light commands |
| **Session summarization** — auto-compress at 85% of context window; structured summary replaces conversation history | No runtime summarization; context only grows within a session; multi-command sessions degrade | High — enables reliable multi-command sessions |
| **Composable middleware** — cross-cutting concerns (audit, PII, write guard) as stackable interceptors | PII guard, write guard, audit logging are inline in Artha.core.md Steps; tightly coupled to catch-up sequence | Medium — enables reuse in new commands |
| **Structured output validation** — Pydantic schema on agent output | Briefing format enforced by prompt template only; occasional drift | Medium — enables programmatic consumption |
| **Pluggable storage backend** — swap in-memory, filesystem, cloud store | Tightly coupled to `state/*.md` filesystem | Low (future) — enables testing, mobile |
| **Sub-agent context isolation** — ephemeral agents with own context windows | All domains processed in one shared context; finance deep-dive consumes tokens irrelevant to health processing | Medium — cleaner per-domain processing |

---

## 3. Pain Points

### P1 — Context Exhaustion on Heavy Days

**Symptom:** When email volume exceeds 200, the catch-up runs in `high` or `extreme` volume tier, applying aggressive compression. Despite this, cross-domain reasoning (Step 8) receives degraded context, producing lower-quality insights and missing compound signals.

**Root cause:** Raw pipeline output (JSONL) and intermediate processing artifacts persist in context long after they're consumed. There's no mechanism to discard consumed intermediate state.

**Measured impact:** `context_pressure: red` or `critical` logged on days with >150 emails. `compound_signals_fired` drops to 0–1 on these days vs. 2–3 on normal days.

### P2 — Multi-Command Session Degradation

**Symptom:** Running `/catch-up` then `/domain finance` then `/items` in a single session causes the third command to operate on significantly less available context than the first.

**Root cause:** Each command's full processing output (briefing, domain analysis, item listing) accumulates in the conversation history. Artha has no session-level summarization or context reclamation.

**Measured impact:** Users frequently open a new session between commands as a workaround.

### P3 — Unnecessary Context Loading for Light Commands

**Symptom:** `/status` or `/items` loads the full Artha.md instruction file (which references all domain prompts), plus 6 always-load state files, even when the command only needs health-check.md and open_items.md.

**Root cause:** No command-level context optimization. The instruction file is monolithic — every command path shares the same context footprint.

**Measured impact:** Light commands use 30K–50K tokens of context before generating a single line of output.

### P4 — Cross-Cutting Concern Coupling

**Symptom:** Adding PII guarding to a new feature (e.g., channel_push.py) requires re-implementing the same pattern. The write guard logic in Step 8b is catch-up-specific but should apply to any state write.

**Root cause:** Guards and hooks are documented as workflow steps, not as reusable abstractions.

**Measured impact:** Development velocity slows; every new write-path requires manually wiring in guards.

### P5 — Briefing Format Drift

**Symptom:** Briefings occasionally deviate from the specified format in `config/briefing-formats.md` — missing sections, wrong ordering, inconsistent emoji usage.

**Root cause:** Format enforcement is purely prompt-based. The LLM generates the briefing in a single pass with no structural validation.

**Measured impact:** Low — cosmetic, but undermines trust in output consistency.

---

## 4. Design Proposals & Trade-off Matrix

### Option A — Minimal Adoption (Context Offloading Only)

Implement only Phase 1 (context offloading) and Phase 5 (structured output). These are the highest-ROI changes with the lowest risk.

| Dimension | Score |
|-----------|-------|
| **Effort** | Low (~2 weeks) |
| **Risk** | Low |
| **Context savings** | 20–40% on heavy days |
| **Multi-command improvement** | None |
| **New capability unlocked** | Structured briefing validation |

### Option B — Core Harness Patterns (Recommended)

Implement Phases 1–5 in sequence. This delivers the three highest-impact improvements (offloading, progressive disclosure, summarization) plus the middleware and validation foundations. Stops short of storage abstraction and sub-agent isolation.

| Dimension | Score |
|-----------|-------|
| **Effort** | Medium (~6–8 weeks) |
| **Risk** | Medium |
| **Context savings** | 40–65% on heavy days, 30–50% on light commands |
| **Multi-command improvement** | Full — session summarization enables 5+ command chains |
| **New capability unlocked** | Middleware reuse, structured output, progressive prompts |

### Option C — Full Harness Adoption

Implement all 7 phases including storage abstraction and sub-agent context isolation. Maximum architectural improvement but introduces significant complexity.

| Dimension | Score |
|-----------|-------|
| **Effort** | High (~12–16 weeks) |
| **Risk** | High — storage abstraction touches every read/write path |
| **Context savings** | 60–80% across all scenarios |
| **Multi-command improvement** | Full |
| **New capability unlocked** | Testable state mocks, mobile-ready backend, per-domain context isolation |

### Recommendation: **Option B** with Phase 6 deferred to a future cycle and Phase 7 as an experimental track.

**Why not Option A:** Offloading alone doesn't solve multi-command degradation (P2) or unnecessary loading (P3). These are the pain points users feel most.

**Why not Option C:** Storage abstraction (Phase 6) requires touching every `state/*.md` read/write path — 31 state files, 18 domain prompts, the vault, the pipeline, and all view scripts. The risk:reward ratio doesn't justify it now. Phase 7 (sub-agent isolation) requires IDE-level support (VS Code sub-agent API) that may change underneath us.

---

## 5. Execution Plan — Strangler Fig Migration

### Execution Sequencing

```
Phase 1 ──────────────────────▶ (context offloading — no dependencies)
Phase 2 ──────────────────────▶ (progressive disclosure — no dependencies)
         ╲
          Phase 3 ────────────▶ (session summarization — benefits from Phase 1+2)
                   ╲
                    Phase 4 ──▶ (middleware — benefits from all prior phases)
                         ╲
                          Phase 5 ▶ (structured output — benefits from Phase 4)
                                    ╲
                                Future: Phase 6 ▶ (storage abstraction)
                                Future: Phase 7 ▶ (sub-task isolation)
```

Phases 1 and 2 are parallel-safe (no dependencies between them).
Phases 3–5 are sequential — each builds on the prior phase.

---

### Phase 1: Context Offloading (Strangler Layer 1)

**Goal:** Automatically offload large intermediate artifacts to `tmp/` files, replacing them in context with a file path + preview. Free 20K–60K tokens on heavy email days.

**Inspired by:** Deep Agents' automatic tool result offloading when output exceeds `tool_token_limit_before_evict` (default 20K tokens).

**Scope:**

| Artifact | Current Behavior | New Behavior |
|----------|-----------------|-------------|
| Pipeline JSONL output (Step 4) | Full stream held in context | Write to `tmp/pipeline_output.jsonl`; inject summary card (source counts, date range, volume tier) |
| Skills cache (Step 4) | Already writes to `tmp/skills_cache.json` ✓ | No change needed — already offloaded |
| Processed email batch (Step 5) | Processed bodies held in context | Write to `tmp/processed_emails.json`; keep only domain-routed summaries in context |
| Domain extraction results (Step 7) | Full extraction output in context | Write per-domain extraction to `tmp/domain_extractions/<domain>.json`; keep only briefing contributions (1–5 bullets) in context |
| Cross-domain reasoning artifacts (Step 8) | Scoring matrices, compound signals in context | Write detailed scoring to `tmp/cross_domain_analysis.json`; keep only the TOP 5 actionable items + ONE THING in context |

**Implementation:**

1. **New module:** `scripts/context_offloader.py`

```python
"""
scripts/context_offloader.py — Context offloading for Artha catch-up workflow.

Writes large intermediate artifacts to tmp/ files and returns a compact
summary card suitable for inclusion in the AI's context window.

Usage:
    from scripts.context_offloader import offload_artifact

    card = offload_artifact(
        name="pipeline_output",
        data=jsonl_records,
        summary_fn=pipeline_summary,
    )
    # card is a short string (< 500 tokens) with path + key stats
"""
```

Core function signature:
```python
def offload_artifact(
    name: str,
    data: Any,
    summary_fn: Callable[[Any], str],
    *,
    threshold_tokens: int = 5_000,
    preview_lines: int = 10,
    artha_dir: Path | None = None,
) -> str:
    """Write data to tmp/{name}.json; return summary card if data > threshold."""
```

2. **Artha.core.md changes:** Update Steps 4, 5, 7, 8 to reference offloaded artifacts:
   - Step 4: "Write pipeline output to `tmp/pipeline_output.jsonl`. In context, keep only: source counts, date range, volume tier, and first 10 records as preview."
   - Step 5: "Write processed emails to `tmp/processed_emails.json`. In context, keep only the routing table output (domain → email count)."
   - Step 7: "Write per-domain extractions to `tmp/domain_extractions/{domain}.json`. In context, keep only the briefing contribution (1–5 bullet points per domain)."
   - Step 8: "Write full scoring analysis to `tmp/cross_domain_analysis.json`. In context, keep only: ONE THING, top 5 alerts, FNA, and compound signals."

3. **Rollback path:** If `context_offloading.enabled: false` in `artha_config.yaml`, all Steps behave exactly as today (no offloading). Default: `true`.

4. **Cleanup:** `tmp/pipeline_output.jsonl`, `tmp/processed_emails.json`, `tmp/domain_extractions/`, and `tmp/cross_domain_analysis.json` are all deleted in Step 18 (ephemeral cleanup). Add them to the existing cleanup block.

**Files changed:**

| File | Change |
|------|--------|
| `scripts/context_offloader.py` | **New** — offload logic |
| `config/Artha.core.md` | Update Steps 4, 5, 7, 8 with offload instructions |
| `config/artha_config.yaml` | Add `context_offloading.enabled: true` |
| `tests/unit/test_context_offloader.py` | **New** — unit tests |

**Tests:**
- `test_offload_below_threshold_returns_raw`: Data < threshold → no file written, returns original data
- `test_offload_above_threshold_writes_file`: Data > threshold → file created, returns summary card
- `test_summary_card_under_500_tokens`: Card output never exceeds 500 tokens
- `test_cleanup_removes_offloaded_files`: Verify Step 18 cleanup path covers new files
- `test_feature_flag_disabled_bypasses_offload`: When config flag is false, no offloading occurs

**Estimated context savings:** 20K–60K tokens on heavy days; 5K–15K tokens on normal days.

**Risks:**
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| AI fails to re-read offloaded file when needed | Medium | High | Summary card includes explicit path + "read this file for full details" instruction. Cross-domain step instructions reference specific file paths. |
| Offloaded file path breaks on Windows (path separators) | Low | Medium | Use `pathlib.Path` throughout; test on Windows CI. |
| Summary card loses critical signal | Medium | Medium | Summary functions are domain-aware; include all P0/P1 signals. Test with sample data from real catch-ups. |

---

### Phase 2: Progressive Domain Disclosure (Strangler Layer 2)

**Goal:** Load domain prompt files (`prompts/*.md`) on-demand instead of unconditionally. Reduce context by 5K–15K tokens for light commands (`/status`, `/items`, `/goals`).

**Inspired by:** Deep Agents' Skills progressive disclosure — only frontmatter loaded at startup; full SKILL.md loaded when the agent determines the skill is relevant.

**Scope:**

| Component | Current | New |
|-----------|---------|-----|
| Domain prompts (`prompts/*.md`) | All 6 Tier A loaded unconditionally; Tier B loaded if emails route | Build a "domain index" from state file frontmatter; load prompt only when processing that domain |
| Instruction file context | Full Artha.md loaded for every command | Add command-level context hints: `/status` needs Steps 0, 2, 16 only; `/items` needs only open_items.md |
| State files (Tier A) | All 6 always loaded | Keep always-load for state *files* but defer prompt loading: state frontmatter (≤200 tokens each) loaded always; full prompt (1K–3K tokens each) loaded only if domain has activity |

**Implementation:**

1. **New module:** `scripts/domain_index.py`

```python
"""
scripts/domain_index.py — Domain index builder for progressive disclosure.

Reads YAML frontmatter from all state/*.md files and produces a compact
domain index card suitable for context injection.

Output format (one line per domain, ~30 tokens each):
    immigration | ACTIVE | last: 2026-03-14 | alerts: 2 🔴 | src: state/immigration.md.age
    finance     | ACTIVE | last: 2026-03-15 | alerts: 1 🟠 | src: state/finance.md.age
    travel      | STALE  | last: 2026-01-10 | alerts: 0    | src: state/travel.md
    estate      | ARCHIVE| last: 2025-09-01 | alerts: 0    | src: state/estate.md.age
"""
```

Core function signature:
```python
def build_domain_index(artha_dir: Path) -> str:
    """Scan state/*.md frontmatter, return compact index card (<1K tokens)."""

def should_load_prompt(domain: str, index: dict, command: str) -> bool:
    """Decide whether to load the full prompts/{domain}.md for this command/context."""
```

2. **Artha.core.md changes:**

   Add a new concept between Steps 4b and 5:

   > **Step 4b′ — Build domain index (progressive disclosure)**
   > Before loading any domain prompt file, build a domain index from state file
   > frontmatter. The index is a compact card (~600 tokens for 18 domains) that
   > lists each domain's status, last activity date, and alert count.
   >
   > Load the full `prompts/{domain}.md` file ONLY when:
   > - Processing that domain in Step 7 (emails routed to it), OR
   > - The domain has alerts from skills (Step 4 skill_runner output), OR
   > - The user explicitly requested that domain (`/domain <name>`), OR
   > - The command requires all domains (`/catch-up deep`)
   >
   > For all other commands (`/status`, `/items`, `/goals`, `/items quick`),
   > use only the domain index — do NOT load individual domain prompts.

   **Command-level hint table:**

   | Command | State files needed | Prompts needed |
   |---------|-------------------|----------------|
   | `/status` | health-check.md | None |
   | `/items` | open_items.md | None |
   | `/items quick` | open_items.md, memory.md (quick_tasks) | None |
   | `/goals` | goals.md | goals.md prompt only |
   | `/domain <X>` | state/{X}.md | prompts/{X}.md only |
   | `/dashboard` | dashboard.md, health-check.md | None |
   | `/catch-up` (standard) | Tier A state files + routed Tier B | Only routed domains |
   | `/catch-up deep` | All state files | All active domain prompts |
   | `/scorecard` | All state files frontmatter | None |

3. **Rollback path:** If `progressive_disclosure.enabled: false` in `artha_config.yaml`, all prompts load unconditionally as today. Default: `true`.

**Files changed:**

| File | Change |
|------|--------|
| `scripts/domain_index.py` | **New** — index builder |
| `config/Artha.core.md` | Add Step 4b′; add command-level hint table |
| `config/artha_config.yaml` | Add `progressive_disclosure.enabled: true` |
| `tests/unit/test_domain_index.py` | **New** — unit tests |

**Tests:**
- `test_index_reads_frontmatter_only`: Verify index doesn't read full state file content
- `test_index_under_1k_tokens`: Index card for 18 domains stays under 1,000 tokens
- `test_should_load_prompt_status_returns_false`: `/status` command → no prompts loaded
- `test_should_load_prompt_catchup_returns_routed_only`: `/catch-up` → only routed domain prompts
- `test_should_load_prompt_domain_returns_specific`: `/domain finance` → only finance prompt
- `test_feature_flag_disabled_loads_all`: When flag is false, all prompts loaded unconditionally

**Estimated context savings:**
- `/status`: ~15K tokens saved (no domain prompts, no state file bodies)
- `/items`: ~12K tokens saved
- `/catch-up` (3 domains active): ~8K tokens saved (skip 3 inactive Tier A prompts)

**Risks:**
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Missing a domain prompt causes extraction failure | Medium | High | The routing step (Step 6) already determines which domains have emails. Prompt loading follows routing output — if emails are routed, prompt is loaded. Safety: any domain with status `ACTIVE` and alerts >0 gets its prompt loaded regardless. |
| Index stale if state file frontmatter not updated | Low | Low | Index is rebuilt fresh every session from file headers. `last_updated` field is mandatory (enforced by post-write verification). |
| `/catch-up flash` loses domain context | Low | Medium | Flash mode is for <4-hour gaps with minimal activity. Domain index provides sufficient context for flash-quality output. |

---

### Phase 3: Session Summarization (Strangler Layer 3)

**Goal:** After each major command completes, compress the command's processing context into a structured session summary. Enable reliable 5+ command chains within a single session.

**Inspired by:** Deep Agents' dual summarization — in-context summary replaces conversation history; full conversation preserved to filesystem. Triggers at 85% of `max_input_tokens`.

**Scope:**

| Scenario | Current | New |
|----------|---------|-----|
| Post-catch-up | Full 50K–200K token conversation history retained | Compress to ~3K session summary; full history preserved in `tmp/session_history.md` |
| Multi-command session | Each command's output accumulates; 3rd command severely degraded | Previous command's context replaced by summary; available context resets to ~80% |
| Context overflow | `context_pressure: critical` triggers P0-only processing | Proactive summarization at 70% threshold prevents reaching critical |

**Implementation:**

1. **New module:** `scripts/session_summarizer.py`

```python
"""
scripts/session_summarizer.py — Session context summarization for Artha.

After a major command completes (catch-up, domain deep-dive, bootstrap),
compresses the command's conversation context into a structured summary
and writes the full history to tmp/ for recovery.

Summary schema:
    session_intent: str         # What the user asked for
    command_executed: str       # Which command ran
    key_findings: list[str]    # Top 5 findings/alerts (max 50 tokens each)
    state_mutations: list[str] # Which state files were modified
    open_threads: list[str]    # Unresolved items needing follow-up
    next_suggested: str        # Recommended next command
    timestamp: str             # ISO-8601
"""
```

2. **Summarization trigger rules (added to Artha.core.md):**

   > **Session Summarization Protocol**
   >
   > After completing any of these commands, generate a session summary
   > and compress context:
   > - `/catch-up` (any format)
   > - `/domain <X>` deep-dive
   > - `/bootstrap` or `/bootstrap <domain>`
   > - `/catch-up deep` (extended briefing)
   >
   > Summarization steps:
   > 1. Generate a structured summary (schema above) of the completed command
   > 2. Write the full conversation history to `tmp/session_history_{N}.md`
   >    (where N is the command sequence number in this session)
   > 3. Replace the conversation context with: session summary + the last
   >    3 user/assistant exchanges (preserved for conversational continuity)
   > 4. Log `summarization_triggered: true` to health-check.md
   >
   > **Proactive trigger:** If estimated context usage reaches 70% of the
   > model's context window at any point, trigger summarization immediately
   > (do not wait for command completion).
   >
   > **Never summarize during:** Active Step 7 domain processing or Step 8
   > cross-domain reasoning — these require full context. Defer until step
   > completes.

3. **Recovery:** If a subsequent command needs details from a summarized command, the AI reads `tmp/session_history_{N}.md`. The summary includes the file path.

4. **Rollback path:** `session_summarization.enabled: false` in `artha_config.yaml`. Default: `true`.

**Files changed:**

| File | Change |
|------|--------|
| `scripts/session_summarizer.py` | **New** — summarization logic + schema |
| `config/Artha.core.md` | Add Session Summarization Protocol section; update catch-up completion flow |
| `config/artha_config.yaml` | Add `session_summarization.enabled: true`, `session_summarization.threshold_pct: 70` |
| `tests/unit/test_session_summarizer.py` | **New** — unit tests |

**Tests:**
- `test_summary_schema_valid`: Generated summary matches Pydantic schema
- `test_summary_under_3k_tokens`: Summary never exceeds 3,000 tokens
- `test_full_history_preserved`: Full conversation written to tmp/ verbatim
- `test_proactive_trigger_at_threshold`: Summarization fires at configured percentage
- `test_no_summarize_during_processing`: Summarization deferred during active Step 7/8
- `test_multi_command_session_context_reset`: After summarization, available context resets to ~80%

**Estimated context savings:** 60–80% context reclaimed between commands. A session that previously degraded after 2 commands can now support 5+.

**Risks:**
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Summary loses critical detail needed by next command | Medium | High | Summary includes `state_mutations` list — the AI knows which files to re-read. `open_threads` preserves unresolved items. Recovery path via `tmp/session_history_{N}.md`. |
| Proactive summarization interrupts active processing | Low | Critical | Hard rule: never summarize during Steps 7 or 8. Defer to step boundary. |
| Token estimation inaccurate | Medium | Low | Use conservative heuristic (1 token ≈ 4 chars). Over-estimating triggers earlier summarization, which is safe (just slightly less context than optimal). |
| Session history files accumulate in tmp/ | Low | Low | All tmp/ files cleaned in Step 18. Max ~5 history files per session (~10K each = 50K disk). |

---

### Phase 4: Middleware Formalization (Strangler Layer 4)

**Goal:** Extract cross-cutting concerns (PII guard, write guard, post-write verification, audit logging) into a composable middleware stack. Enable reuse across all commands and new features.

**Inspired by:** Deep Agents' middleware architecture — `AgentMiddleware` hooks intercept tool calls for logging, validation, and transformation. Each middleware is independent and stackable.

**Scope:**

| Concern | Current Location | New Location |
|---------|-----------------|-------------|
| PII guard | Step 5b (catch-up only) | `scripts/middleware/pii_middleware.py` — wraps any state read/write |
| Net-negative write guard | Step 8b (catch-up only) | `scripts/middleware/write_guard.py` — wraps any state write |
| Post-write verification | Step 8c (catch-up only) | `scripts/middleware/write_verify.py` — runs after any state write |
| Audit logging | Scattered across Steps 17, 8b, 8c, etc. | `scripts/middleware/audit_middleware.py` — decorates any state mutation |
| Rate limiting | Not implemented | `scripts/middleware/rate_limiter.py` — wraps external API calls |

**Implementation:**

1. **New package:** `scripts/middleware/`

```
scripts/middleware/
├── __init__.py          # Registry + compose() function
├── pii_middleware.py    # PII scan before write
├── write_guard.py       # Net-negative write guard
├── write_verify.py      # Post-write integrity verification
├── audit_middleware.py   # Audit trail logging
└── rate_limiter.py      # API rate limiting (new capability)
```

2. **Middleware interface:**

```python
class StateMiddleware(Protocol):
    """Protocol for state file read/write interceptors."""

    def before_write(
        self, domain: str, current_content: str, proposed_content: str
    ) -> str | None:
        """Called before a state file write.

        Returns:
            Modified content to write, or None to block the write.
        """
        ...

    def after_write(self, domain: str, file_path: Path) -> None:
        """Called after a state file write succeeds."""
        ...
```

3. **Composition:**

```python
def compose_middleware(
    middlewares: list[StateMiddleware],
) -> StateMiddleware:
    """Chain multiple middleware into a single pipeline.

    Execution order: before_write runs left-to-right (PII → guard → audit).
    after_write runs right-to-left (audit → verify → log).
    """
```

4. **Artha.core.md changes:** Steps 5b, 8b, 8c refactored to reference middleware:

   > **State Write Protocol (replaces inline guards)**
   >
   > All state file writes — from any command, not just catch-up — pass through
   > the middleware stack: `PII → WriteGuard → AuditLog → [write] → WriteVerify → AuditLog`
   >
   > This replaces the inline guards previously documented in Steps 5b, 8b, 8c.
   > The guards' behavior is identical; only their invocation point changes.

5. **Rate limiter (new):**

```python
class RateLimiter(StateMiddleware):
    """Enforces per-provider API rate limits.

    Reads limits from config/connectors.yaml per-connector:
        gmail:
          rate_limit: {calls_per_minute: 30, burst: 10}
    """
```

**Files changed:**

| File | Change |
|------|--------|
| `scripts/middleware/__init__.py` | **New** — registry + compose |
| `scripts/middleware/pii_middleware.py` | **New** — extracted from pii_guard.py |
| `scripts/middleware/write_guard.py` | **New** — extracted from Step 8b logic |
| `scripts/middleware/write_verify.py` | **New** — extracted from Step 8c logic |
| `scripts/middleware/audit_middleware.py` | **New** — extracted from scattered audit log calls |
| `scripts/middleware/rate_limiter.py` | **New** — new capability |
| `config/Artha.core.md` | Replace Steps 5b, 8b, 8c with State Write Protocol |
| `config/artha_config.yaml` | Add `middleware.enabled: true`, per-middleware enable flags |
| `tests/unit/test_middleware.py` | **New** — unit tests for composition + each middleware |

**Tests:**
- `test_compose_runs_before_write_in_order`: Verify left-to-right execution
- `test_pii_middleware_blocks_raw_ssn`: PII detected → content redacted before write
- `test_write_guard_blocks_over_20pct_loss`: Net-negative guard fires correctly
- `test_write_verify_catches_missing_frontmatter`: Post-write check catches invalid files
- `test_audit_middleware_logs_all_mutations`: Audit trail written for every write
- `test_rate_limiter_delays_on_burst`: Rate limiter enforces per-provider limits
- `test_middleware_disabled_by_flag`: When `middleware.enabled: false`, all writes pass through unmodified
- `test_bootstrap_exempt_from_write_guard`: Files with `updated_by: bootstrap` skip net-negative check

**Risks:**
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Middleware ordering bug causes silent data loss | Low | Critical | Extensive unit tests for composition order. `before_write` returns `None` to block — no silent pass-through. |
| Performance overhead on hot path | Low | Low | Guards are pure string operations (field counting, regex). Sub-millisecond. |
| Extracting inline logic introduces behavioral differences | Medium | Medium | Test with identical inputs to current Steps 5b, 8b, 8c. Use golden-file testing: capture current output, verify middleware produces identical output. |

---

### Phase 5: Structured Output Validation (Strangler Layer 5)

**Goal:** Define Pydantic schemas for briefing sections and validate output structure before presenting to the user. Enable programmatic consumption of briefings.

**Inspired by:** Deep Agents' `response_format` parameter — Pydantic schema validation on agent output ensures consistent structure.

**Scope:**

| Output | Current | New |
|--------|---------|-----|
| Standard briefing | Free-form Markdown, template-prompted | Validated against `BriefingSchema` |
| Flash briefing | Free-form Markdown | Validated against `FlashBriefingSchema` |
| Domain index (Phase 2) | N/A | Validated against `DomainIndexSchema` |
| Session summary (Phase 3) | N/A | Validated against `SessionSummarySchema` |

**Implementation:**

1. **New module:** `scripts/schemas/`

```python
# scripts/schemas/briefing.py
from pydantic import BaseModel, Field

class AlertItem(BaseModel):
    severity: Literal["critical", "urgent", "standard", "info"]
    domain: str
    description: str = Field(max_length=200)
    score: int | None = Field(default=None, description="U×I×A score")

class DomainSummary(BaseModel):
    domain: str
    bullet_points: list[str] = Field(max_length=5)
    alert_count: int = Field(ge=0)

class BriefingOutput(BaseModel):
    one_thing: str = Field(max_length=300)
    critical_alerts: list[AlertItem]
    urgent_alerts: list[AlertItem]
    domain_summaries: list[DomainSummary]
    open_items_added: int
    open_items_closed: int
    fna: str | None = None
    pii_footer: str
```

2. **Validation step added after Step 11 (briefing synthesis):**

   > **Step 11b — Briefing validation**
   > After synthesizing the briefing Markdown, extract structured data into
   > `BriefingOutput` schema. Validate:
   > - At least one domain summary present
   > - All severity levels are valid enum values
   > - ONE THING is non-empty and under 300 characters
   > - PII footer is present
   >
   > If validation fails, log the failure to `state/audit.md` and present
   > the briefing anyway (graceful degradation — never block output).
   >
   > Write validated structured data to `tmp/briefing_structured.json` for
   > programmatic consumption (channel push, dashboard rebuild, /diff).

3. **Downstream consumers:**
   - `channel_push.py` reads `tmp/briefing_structured.json` instead of parsing Markdown
   - `/diff` command compares structured briefings across days
   - Dashboard rebuild (Step 8h) uses structured data

**Files changed:**

| File | Change |
|------|--------|
| `scripts/schemas/__init__.py` | **New** — package init |
| `scripts/schemas/briefing.py` | **New** — briefing schemas |
| `scripts/schemas/session.py` | **New** — session summary schema (from Phase 3) |
| `scripts/schemas/domain_index.py` | **New** — domain index schema (from Phase 2) |
| `config/Artha.core.md` | Add Step 11b |
| `config/artha_config.yaml` | Add `structured_output.enabled: true` |
| `tests/unit/test_schemas.py` | **New** — schema validation tests |

**Tests:**
- `test_briefing_schema_valid_example`: Known-good briefing validates
- `test_briefing_schema_rejects_missing_one_thing`: Missing ONE THING fails validation
- `test_briefing_schema_rejects_invalid_severity`: Invalid severity enum fails
- `test_validation_failure_graceful`: Failed validation still produces output + audit log entry
- `test_structured_json_written`: Validated briefing written to tmp/

**Risks:**
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Schema too strict, causes constant validation failures | Medium | Low | Schema is validated but never blocks output. Failures logged for iterative schema refinement. Start with loose constraints, tighten over 2–3 cycles. |
| Pydantic dependency version conflict | Low | Low | Already in `requirements.txt` for other uses. Pin compatible version. |

---

### Phase 6: State Abstraction Layer (Strangler Layer 6) — DEFERRED

> **Status: Deferred to future cycle.** Included here for architectural completeness.

**Goal:** Abstract state file access behind a `StateStore` interface so the backend (filesystem, cloud, in-memory mock) is swappable.

**Why deferred:** Touches every read/write path in the system (31 state files, 18 domain prompts, vault, pipeline, 4 view scripts). Risk:reward ratio doesn't justify it now. Current filesystem approach works and is human-debuggable.

**Pre-conditions for starting Phase 6:**
- Phases 1–5 complete and stable (3+ weeks in production)
- Middleware layer (Phase 4) absorbs all guard/verification logic — no more inline state writes
- Test coverage for all state read/write paths ≥90%

**Interface sketch (for future reference):**

```python
class StateStore(Protocol):
    def read(self, domain: str) -> str: ...
    def write(self, domain: str, content: str) -> None: ...
    def read_frontmatter(self, domain: str) -> dict[str, Any]: ...
    def list_domains(self) -> list[str]: ...
    def exists(self, domain: str) -> bool: ...

class FilesystemStateStore:
    """Current behavior — reads/writes state/*.md files on disk."""

class InMemoryStateStore:
    """For testing — no disk I/O."""
```

---

### Phase 7: Sub-Task Context Isolation (Strangler Layer 7) — EXPERIMENTAL

> **Status: Experimental / future.** Depends on IDE sub-agent capabilities stabilizing.

**Goal:** Process each domain in its own isolated context window. The orchestrator receives only the domain's briefing contribution (1–5 bullets), never the raw extraction context.

**Why experimental:** This requires IDE-level sub-agent support (VS Code's `runSubagent` tool or equivalent). The API is not yet stable across all AI CLIs Artha supports (Claude Code, Copilot, Gemini CLI). The benefit is real but the implementation surface is volatile.

**Conceptual model:**

```
Orchestrator (main context):
    Step 6: Route emails → domain assignments
    Step 7a: For each domain with activity:
              spawn sub-task(domain, emails, state_file, prompt)
              receive: { briefing_bullets: [...], alerts: [...], state_delta: {...} }
    Step 7b: Merge all sub-task outputs
    Step 8:  Cross-domain reasoning on merged outputs only
```

**Pre-conditions:**
- Phases 1–4 complete (offloading + middleware provide the infrastructure)
- VS Code sub-agent API stable for 2+ releases
- Multi-CLI compatibility tested (Claude Code, Copilot, Gemini CLI)

---

## 6. Risk Register

### Architecture-Level Risks

| ID | Risk | Phase | Likelihood | Impact | Mitigation |
|----|------|-------|-----------|--------|-----------|
| R1 | Context offloading causes AI to "forget" critical signals | 1 | Medium | High | Summary cards include all P0/P1 signals. Explicit file-path references. Golden-file tests with real catch-up data. |
| R2 | Progressive disclosure skips a domain that has urgent signals | 2 | Medium | High | Routing step (Step 6) runs BEFORE disclosure decision. Any domain with routed emails gets its prompt loaded. Skills output also triggers prompt loading. |
| R3 | Session summarization loses context needed for follow-up questions | 3 | Medium | Medium | Full history preserved in `tmp/session_history_{N}.md`. Summary includes `open_threads` list. User can ask "show me the full history" → AI reads the file. |
| R4 | Middleware ordering bug causes silent data corruption | 4 | Low | Critical | Composition function enforces order. Before_write returns `None` to block (not silent pass-through). Golden-file tests capture current behavior. |
| R5 | Structured output schema too rigid for prompt-driven system | 5 | Medium | Low | Validation never blocks output. Schema starts loose, tightens iteratively. |
| R6 | Feature flag proliferation makes config unwieldy | All | Medium | Low | Group all flags under `harness:` namespace. Max 7 flags total. Each flag defaults to `true` after phase stabilizes. |
| R7 | Phases interact unexpectedly (offloading + summarization conflict) | 1+3 | Low | Medium | Offloaded files are tmp/ artifacts; summarization compresses conversation context. Different layers — no conflict in data path. Integration test validates combined behavior. |
| R8 | Windows/Linux path handling in offloader and middleware | 1, 4 | Low | Medium | All paths via `pathlib.Path`. CI tests on macOS. Manual validation on Windows before release. |

### Operational Risks

| ID | Risk | Mitigation |
|----|------|-----------|
| R9 | Rollout causes regression in existing catch-up quality | Each phase has a feature flag (disabled = old behavior). Phases roll out one at a time with 1-week stabilization before enabling next phase. |
| R10 | User confusion about offloaded files in tmp/ | All tmp/ files cleaned in Step 18. No user-visible impact unless they inspect tmp/ during a session. |
| R11 | Test suite doesn't catch prompt-level regressions | Add 3 "golden briefing" integration tests: feed a known email dataset, verify briefing output matches expected structure. |
| R12 | Backward-incompatible changes to artha_config.yaml | All new config keys have defaults that preserve current behavior. Missing keys → default values → no breakage. |

---

## 7. Success Criteria

### Quantitative Metrics (measured via `state/health-check.md`)

| Metric | Current Baseline | Phase 1 Target | Phase 1–3 Target | Phase 1–5 Target |
|--------|-----------------|---------------|-----------------|-----------------|
| `context_pressure: critical` rate | ~15% of catch-ups (estimated) | < 5% | < 2% | < 1% |
| `context_pressure: red` rate | ~25% of catch-ups | < 10% | < 5% | < 3% |
| `compound_signals_fired` (avg on heavy days) | 0–1 | 1–2 | 2–3 | 2–3 |
| Multi-command session viability | ~2 commands before degradation | 2–3 | 5+ | 5+ |
| Context tokens saved per catch-up | 0 | 20K–40K | 40K–80K | 50K–90K |
| `/status` context footprint | ~30K tokens | ~30K | ~15K | ~12K |
| Test count (total) | 541 | 560+ | 590+ | 620+ |
| Briefing validation pass rate | N/A | N/A | N/A | >90% |

### Qualitative Criteria

| Criterion | Verification |
|-----------|-------------|
| No catch-up regression | Run 5 catch-ups with each phase enabled; compare output quality against pre-phase briefings. |
| Feature flags work as documented | Disable each flag; verify behavior reverts to pre-phase state. |
| All new code has tests | `make test` passes; coverage report confirms new modules covered. |
| Artha.core.md remains coherent | The instruction file reads as a unified document, not a patchwork. |
| No PII leakage via offloaded files | Offloaded files in tmp/ run through PII guard before write. Verified by test. |

---

## 8. Dependency Map

```
Phase 1 (Context Offloading)     Phase 2 (Progressive Disclosure)
     │                                │
     │  (no dependency)               │
     │                                │
     └──────────┬─────────────────────┘
                │
                ▼
     Phase 3 (Session Summarization)
        Benefits from Phases 1+2 (less to summarize)
                │
                ▼
     Phase 4 (Middleware Formalization)
        Benefits from Phase 3 (middleware can trigger summarization)
                │
                ▼
     Phase 5 (Structured Output)
        Benefits from Phase 4 (validation is a middleware)
                │
                ▼ (future)
     Phase 6 (State Abstraction)
        Requires Phase 4 middleware to absorb all inline guards
                │
                ▼ (future)
     Phase 7 (Sub-Task Isolation)
        Requires Phases 1-4 as infrastructure
```

### External Dependencies

| Dependency | Required By | Risk |
|-----------|-------------|------|
| `pydantic` | Phase 5 | Already in requirements.txt |
| `pathlib` | Phases 1, 2, 4 | stdlib — zero risk |
| `yaml` (PyYAML) | Phase 2 | Already in requirements.txt |
| VS Code sub-agent API | Phase 7 | External, unstable — deferred |

### Internal Dependencies

| Module | Depends On | Depended On By |
|--------|-----------|---------------|
| `context_offloader.py` | None | Phase 3 (summarizer reads offloaded paths) |
| `domain_index.py` | `profile_loader.py` (domain_registry) | Phase 3 (summarizer uses index for context estimation) |
| `session_summarizer.py` | Phases 1, 2 (for optimal benefit) | None |
| `middleware/` | `pii_guard.py`, `vault.py` (extracted logic) | Phase 5 (validation as middleware) |
| `schemas/` | None | `channel_push.py`, dashboard rebuild, `/diff` |

---

## 9. Backward Compatibility Contract

### Zero-Downtime Migration

Each phase is enabled via a feature flag in `config/artha_config.yaml`. The flag defaults to `false` during development and flips to `true` after stabilization testing.

```yaml
# config/artha_config.yaml additions
harness:
  context_offloading:
    enabled: true              # Phase 1
    threshold_tokens: 5000     # Minimum size before offloading
  progressive_disclosure:
    enabled: true              # Phase 2
  session_summarization:
    enabled: true              # Phase 3
    threshold_pct: 70          # Context % that triggers proactive summarization
  middleware:
    enabled: true              # Phase 4
    pii: true
    write_guard: true
    write_verify: true
    audit: true
    rate_limiter: true
  structured_output:
    enabled: true              # Phase 5
    strict_mode: false         # true = block on validation failure (future)
```

### Invariants (never broken)

1. `state/*.md` file format unchanged — YAML frontmatter + Markdown body
2. `config/connectors.yaml` schema unchanged
3. `config/skills.yaml` schema unchanged
4. `pipeline.py` CLI interface unchanged (`--since`, `--source`, `--health`, etc.)
5. `skill_runner.py` CLI interface unchanged
6. `vault.py` encrypt/decrypt behavior unchanged
7. All existing commands (`/catch-up`, `/status`, `/items`, etc.) produce functionally equivalent output
8. `state/health-check.md` schema is additive only — new fields added, no fields removed
9. `state/audit.md` format unchanged — new event types added, no types removed
10. `tmp/` remains ephemeral — all new files cleaned in Step 18

### Migration Testing Protocol

For each phase, before flipping the feature flag to `true` in production:

1. Run `make test` — all existing tests pass
2. Run 3 catch-ups with flag `false` — capture output as baseline
3. Run 3 catch-ups with flag `true` — compare output against baseline
4. Verify: no alerts dropped, no domains skipped, no data loss
5. Run `/status`, `/items`, `/goals`, `/dashboard` — verify all produce correct output
6. Flip flag to `true` in tracked config commit

---

## 10. Observability Strategy

### Metrics (added to `state/health-check.md`)

```yaml
# New fields under catch_up_runs:
harness_metrics:
  context_offloading:
    artifacts_offloaded: 4          # count of offloaded artifacts this session
    tokens_offloaded: 35000         # total tokens saved via offloading
    files_written: ["tmp/pipeline_output.jsonl", "tmp/processed_emails.json", ...]
  progressive_disclosure:
    prompts_loaded: 3               # domain prompts actually loaded
    prompts_skipped: 5              # domain prompts deferred
    tokens_saved: 12000             # estimated tokens saved
  session_summarization:
    triggered: true                 # whether summarization ran this session
    trigger_reason: "post_command"  # or "proactive_threshold"
    context_before_pct: 85          # context usage before summarization
    context_after_pct: 25           # context usage after summarization
    history_file: "tmp/session_history_1.md"
  middleware:
    pii_invocations: 8
    write_guard_blocks: 0
    write_verify_failures: 0
    audit_entries_written: 12
    rate_limit_delays: 0
  structured_output:
    validation_passed: true
    validation_errors: []
    structured_file: "tmp/briefing_structured.json"
```

### Alerts (surfaced in `/health` command)

| Condition | Alert |
|-----------|-------|
| `context_offloading.artifacts_offloaded == 0` on a day with >100 emails | "⚠️ Context offloading did not trigger despite heavy email volume" |
| `progressive_disclosure.prompts_loaded == total_domains` | "ℹ️ Progressive disclosure loaded all prompts — review hint table" |
| `session_summarization.context_after_pct > 60` | "⚠️ Session summarization recovered less context than expected" |
| `middleware.write_guard_blocks > 3` in one session | "🔴 Multiple net-negative write blocks — state files may need manual review" |
| `structured_output.validation_passed == false` for 3+ consecutive catch-ups | "⚠️ Briefing structure consistently failing validation — review schema" |

### Audit Trail

All new modules log to `state/audit.md` using existing conventions:

```
[2026-03-15T14:30:00Z] CONTEXT_OFFLOAD | artifact: pipeline_output | tokens: 25000 | path: tmp/pipeline_output.jsonl
[2026-03-15T14:30:05Z] PROGRESSIVE_DISCLOSURE | loaded: [immigration, finance, health] | skipped: [travel, shopping, home, estate, vehicle]
[2026-03-15T14:35:00Z] SESSION_SUMMARIZE | reason: post_command | before_pct: 82 | after_pct: 22 | history: tmp/session_history_1.md
[2026-03-15T14:35:01Z] MIDDLEWARE_WRITE | domain: finance | pii_redacted: 2 | guard_passed: true | verify_passed: true
[2026-03-15T14:36:00Z] STRUCTURED_VALIDATE | passed: true | errors: 0
```

---

## Appendix A: Deep Agents Feature Mapping

Complete mapping of Deep Agents capabilities to Artha adoption status:

| Deep Agents Feature | Artha Equivalent | Adoption Phase | Notes |
|---------------------|-----------------|---------------|-------|
| `write_todos` tool | Steps 7b, 15 (open_items.md + MS To Do sync) | N/A — already implemented | Stronger: Artha syncs to external To Do |
| `read_file`, `write_file`, `edit_file` | Direct filesystem ops via AI CLI | N/A — native to AI CLI runtimes | Not applicable — Artha is prompt-driven |
| `execute` shell tool | Scripts called from Artha.md steps | N/A — native to AI CLI runtimes | Not applicable |
| `task` sub-agent spawning | Not implemented | Phase 7 (deferred) | Requires stable sub-agent API across CLIs |
| Tool result offloading (>20K tokens) | Not implemented | **Phase 1** | Adapted for pipeline/email intermediate data |
| Automatic summarization (85% threshold) | Not implemented | **Phase 3** | Adapted for session-level summarization |
| Skills progressive disclosure | Not implemented (prompts load unconditionally) | **Phase 2** | Applied to domain prompts, not AI skills |
| Middleware composition | Informal (inline guards) | **Phase 4** | Formalized with Protocol interface |
| `response_format` structured output | Not implemented | **Phase 5** | Pydantic validation on briefings |
| Pluggable filesystem backends | Not applicable (filesystem is the data store) | Phase 6 (deferred) | Future: `StateStore` abstraction |
| Long-term memory (LangGraph Store) | `state/memory.md` + `state/health-check.md` | N/A — already implemented | Artha's approach is simpler but sufficient |
| Human-in-the-loop (`interrupt_on`) | Step 13 (action proposals require approval) | N/A — already implemented | Artha's user-approval model is equivalent |
| Connection resilience (retry + backoff) | `lib/retry.py` in pipeline.py | N/A — already implemented | Works identically |
| AGENTS.md memory files | `config/Artha.md` + `state/memory.md` | N/A — already implemented | Artha's instruction file IS the AGENTS.md |
| Model-agnostic (provider swap) | Multi-CLI support (Claude, Copilot, Gemini) | N/A — already implemented | Different approach (CLI swap vs. model swap) but same outcome |

## Appendix B: Config Schema Additions

Full `artha_config.yaml` schema for harness features:

```yaml
# ── Deep Agents Harness Adoption ──────────────────
# All flags default to true after phase stabilization.
# Set to false to revert to pre-phase behavior.
harness:
  # Phase 1: Write large intermediate data to tmp/ files
  context_offloading:
    enabled: true
    threshold_tokens: 5000        # Minimum artifact size before offloading
    preview_lines: 10             # Lines included in summary card

  # Phase 2: Load domain prompts on-demand
  progressive_disclosure:
    enabled: true
    # Domains that always load their prompt (overrides lazy behavior)
    force_load: []                # e.g., ["immigration"] to always load

  # Phase 3: Compress context between commands
  session_summarization:
    enabled: true
    threshold_pct: 70             # Proactive trigger at N% of context window
    preserve_recent_exchanges: 3  # Keep last N user/assistant turns

  # Phase 4: Composable state write interceptors
  middleware:
    enabled: true
    pii: true                     # PII redaction before write
    write_guard: true             # Net-negative write protection
    write_verify: true            # Post-write integrity check
    audit: true                   # Audit trail logging
    rate_limiter: true            # API rate limiting

  # Phase 5: Pydantic validation on output
  structured_output:
    enabled: true
    strict_mode: false            # true = fail on invalid output (future)
```

## Appendix C: File Manifest

New files introduced across all phases:

```
scripts/
├── context_offloader.py          # Phase 1
├── domain_index.py               # Phase 2
├── session_summarizer.py         # Phase 3
├── schemas/                      # Phase 5
│   ├── __init__.py
│   ├── briefing.py
│   ├── session.py
│   └── domain_index.py
└── middleware/                   # Phase 4
    ├── __init__.py
    ├── pii_middleware.py
    ├── write_guard.py
    ├── write_verify.py
    ├── audit_middleware.py
    └── rate_limiter.py

tests/unit/
├── test_context_offloader.py     # Phase 1
├── test_domain_index.py          # Phase 2
├── test_session_summarizer.py    # Phase 3
├── test_middleware.py            # Phase 4
└── test_schemas.py               # Phase 5
```

Modified files:

```
config/Artha.core.md              # Steps 4, 4b′, 5, 7, 8, 11b updated
config/artha_config.yaml          # harness: namespace added
config/implementation_status.yaml # New feature entries
```

---

> **Next steps:** Review this spec. Approve Option B scope. Begin Phase 1 implementation.
> Phases 1 and 2 can begin in parallel.
