# Artha — Agentic Reloaded: Intelligence Amplification Plan

> **Version**: 1.0 | **Status**: Proposed | **Date**: March 15, 2026
> **Author**: Lead Principal System Architect | **Classification**: Internal — Architecture
> **Triggered by**: Framework Deconstruction Analysis (Hermes Agent, OpenClaw) + prior framework analysis (AutoGen, SK, LangChain/LangGraph, Pydantic AI, OpenAI Agents SDK, Agno)
> **Related**: [specs/deep-agents.md](deep-agents.md) (DA-1–5, implemented), [specs/agentic-improve.md](agentic-improve.md) (AI-1–5, implemented), [specs/vm-hardening.md](vm-hardening.md) (VM-0–5, implemented)
> **Supersedes**: agentic-improve.md Phase 6 (Sub-Agent Isolation — original design updated here)
> **Methodology**: Strangler Fig incremental migration, DDD bounded contexts, SOLID/SRP

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [AS-IS Analysis — Foundation Audit](#2-as-is-analysis--foundation-audit)
3. [Gap Analysis — What's Missing](#3-gap-analysis--whats-missing)
4. [Source Primitives — Hermes Agent + OpenClaw Extraction](#4-source-primitives--hermes-agent--openclaw-extraction)
5. [Execution Plan — Strangler Fig Phases](#5-execution-plan--strangler-fig-phases)
   - [Phase AR-1: Bounded Memory & Consolidation Discipline](#phase-ar-1-bounded-memory--consolidation-discipline)
   - [Phase AR-2: Self-Model (AI Metacognition)](#phase-ar-2-self-model-ai-metacognition)
   - [Phase AR-3: Pre-Eviction Memory Flush](#phase-ar-3-pre-eviction-memory-flush)
   - [Phase AR-4: Session Search & Cross-Session Recall](#phase-ar-4-session-search--cross-session-recall)
   - [Phase AR-5: Procedural Memory (Learned Skills)](#phase-ar-5-procedural-memory-learned-skills)
   - [Phase AR-6: Prompt Stability Architecture](#phase-ar-6-prompt-stability-architecture)
   - [Phase AR-7: Delegation Protocol Formalization](#phase-ar-7-delegation-protocol-formalization)
   - [Phase AR-8: Root-Cause Before Retry Protocol](#phase-ar-8-root-cause-before-retry-protocol)
6. [Risk Register](#6-risk-register)
7. [Trade-Off Decision Log](#7-trade-off-decision-log)
8. [Dependency Map & Critical Path](#8-dependency-map--critical-path)
9. [Backward Compatibility Contract](#9-backward-compatibility-contract)
10. [Observability Strategy](#10-observability-strategy)
11. [Success Criteria](#11-success-criteria)
12. [Assumptions & Constraints](#12-assumptions--constraints)

---

## 1. Executive Summary

### Context

Three prior spec generations have been fully implemented:

| Spec | Phases | Lines | Tests | Status |
|------|:------:|:-----:|:-----:|--------|
| deep-agents.md (DA-1–5) | 5 | ~1,500 | 698→816 | ✅ Complete |
| vm-hardening.md (VM-0–5) | 6 | ~1,600 | 816→804¹ | ✅ Complete |
| agentic-improve.md (AI-1–5) | 5 | ~650 | 816→936 | ✅ Complete |

¹ Net count reflects test restructuring; coverage increased.

**Total delivered: ~3,750+ lines of production code, 938 tests (incl. v7.0.1 patch), 15+ modules.**

Two open-source agent systems — **Hermes Agent** (7.5K★, Nous Research) and **OpenClaw** (314K★, its predecessor) — were deeply analyzed to extract architectural primitives. Hermes Agent is the most advanced open-source AI agent: closed learning loop, skills-from-experience, dual-peer dialectic memory, isolated subagent delegation, frozen snapshot caching, session lineage, and built-in RL training.

This spec distills **10 framework-agnostic primitives** from that analysis and maps them against Artha's existing codebase. After auditing implemented code, **5 primitives are partially covered** by existing work. The remaining gaps are consolidated into **8 incremental phases** — 3 are prompt-only (zero code), 3 are lightweight code+prompt, and 2 are structural enhancements.

### What This Plan Addresses

| Gap | Primitive Source | How Covered Today | What's Missing |
|-----|-----------------|-------------------|----------------|
| Memory has no capacity limit | Hermes MEMORY.md (2,200 char cap) | `state/memory.md` exists with v2.0 schema, `fact_extractor.py` writes facts | No hard cap, no consolidation pressure, no overflow protection |
| No AI self-awareness | Hermes dual-peer modeling | `config/user_profile.yaml` covers user | No agent self-model — blind spots, effective strategies, domain confidence |
| Facts lost during compression | Hermes pre-compression flush | `session_summarizer.py` compresses at 70% | No "persist before compress" step |
| No cross-session recall | Hermes FTS5 + session_search | `summaries/` stores briefings, `session_summarizer.py` writes to `tmp/` | No search over historical summaries/briefings |
| No procedural memory | Hermes skill_manage (skills-from-experience) | `config/skills.yaml` has author-curated data skills | No agent-created procedures from successful complex tasks |
| Prompt caching not optimized | Hermes prompt assembly (cached vs. ephemeral split) | `generate_identity.py` produces compact mode (~15KB) | No explicit frozen/ephemeral layer separation strategy |
| No delegation protocol | Hermes delegate_task (summary-only returns) | Functional delegation exists (backup→vault, pipeline→connectors) | No formalized sub-agent handoff with context compression |
| Blind retry on failures | Hermes root-cause-before-retry | Some error handling in connectors | No systematic "diagnose before retry" protocol |

### What This Is NOT

- **Not a framework adoption.** Zero external framework dependencies.
- **Not a rewrite.** Every phase extends existing code through the Strangler Fig pattern.
- **Not speculative.** Each primitive is proven in production by Hermes Agent (7.5K★, 103 contributors) or validated by Artha's own 938-test suite.
- **Not disruptive.** Phases AR-1, AR-6, and AR-8 are prompt-only. No code changes. Ship today.

### Design Philosophy Reminder (Tech Spec §1.1)

1. **Claude Code IS the application** — no custom runtime
2. **Prompts are the logic layer** — code supports prompts, not the other way around
3. **State in Markdown** — human-readable, diffable, version-controlled
4. **Zero custom code is the target** — code exists only where prompts can't reach

---

## 2. AS-IS Analysis — Foundation Audit

### Implemented Capabilities Map

```
┌─────────────────────────────────────────────────────────────────┐
│              ARTHA AGENTIC INTELLIGENCE STACK                   │
│              (as of v7.0.1, March 15, 2026)                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  LAYER 5: REASONING                                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ OODA Protocol (AI-1) — prompt-based, audit-verified      │   │
│  │ Cross-domain connection matrix (8 mandatory pairs)       │   │
│  │ U×I×A scoring (Urgency × Impact × Actionability)        │   │
│  │ ONE THING selection + FNA pipeline                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  LAYER 4: KNOWLEDGE & MEMORY                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Persistent facts (AI-5) — fact_extractor.py, memory.md   │   │
│  │ Progressive domain disclosure (DA-2) — domain_index.py   │   │
│  │ Session summarization (DA-3) — session_summarizer.py     │   │
│  │ User profile — config/user_profile.yaml                  │   │
│  │ 12 data skills — config/skills.yaml                      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  LAYER 3: CONTEXT MANAGEMENT                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Tiered eviction (AI-2) — 4-tier EvictionTier enum        │   │
│  │ Context offloading (DA-1) — artifacts > 5K to tmp/       │   │
│  │ ArthaContext carrier (AI-3) — pressure, connectors       │   │
│  │ Checkpoints (AI-4) — tmp/.checkpoint.json, 4hr TTL       │   │
│  │ Compact mode — generate_identity.py (~15KB instruction)  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  LAYER 2: SAFETY & INTEGRITY                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Middleware stack (DA-4) — 5 composable middlewares        │   │
│  │ Structured output (DA-5) — Pydantic schemas, non-blocking│   │
│  │ PII guard — regex + semantic redaction                    │   │
│  │ Vault encryption — age-based, keyring-backed             │   │
│  │ Audit trail — append-only state/audit.md                 │   │
│  │ Net-negative write guard — blocks data loss writes       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  LAYER 1: INFRASTRUCTURE                                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Environment detection (VM-1) — 7-probe manifest          │   │
│  │ Preflight hardening (VM-2) — advisory mode, profile check│   │
│  │ Workflow phase gates (VM-4) — 5 workflow files, ⛩️ gates │   │
│  │ Pipeline — 8 connectors, ThreadPoolExecutor              │   │
│  │ Compliance audit (VM-5) — 7 weighted checks              │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Bottleneck: Intelligence Ceiling

The stack above is solid for **executing a known workflow** (catch-up). It is weak at:

1. **Learning from mistakes** — facts extract post-session but there's no procedural memory ("how I solved X last time")
2. **Cross-session continuity** — summaries exist in `summaries/` and `briefings/` but are write-only, never searched
3. **Self-calibration** — no agent self-awareness of its own strengths/weaknesses per domain
4. **Context economics** — no awareness that system prompt stability affects provider cache hit rates
5. **Graceful degradation under failure** — retry logic exists but no root-cause-first protocol

These are exactly the gaps Hermes Agent has solved. This plan ports those solutions to Artha's prompt-driven model.

---

## 3. Gap Analysis — What's Missing

### Detailed Gap Table

| # | Gap | Evidence | Impact | Hermes Equivalent |
|:-:|-----|----------|--------|-------------------|
| G1 | `state/memory.md` has no capacity limit | `facts: []` with no max entries, no consolidation instruction | Memory grows unbounded → eventually dominates context | MEMORY.md: 2,200 char hard cap, agent must merge when full |
| G2 | No AI self-model | grep for `self_model` returns 0 production hits | Agent doesn't know its own blind spots → repeats mistakes | Dual-peer: AI builds representation of itself via `observe_me=True` |
| G3 | No pre-eviction memory flush | grep for `pre.eviction\|pre.compression\|memory.flush` returns 0 hits | Facts from middle turns lost during session compression | One-turn flush before compression: "persist anything important now" |
| G4 | No cross-session search | grep for `FTS5\|fts5\|full.text.search` returns 0 hits | Summaries in `summaries/` are write-only archives | `session_search` tool: FTS5 over SQLite + LLM summarization |
| G5 | No procedural memory | grep for `learned_procedure` returns 0 hits; `state/learned_procedures/` doesn't exist | Agent re-discovers same solutions every session | `skill_manage`: auto-create skills after 5+ step tasks |
| G6 | No explicit prompt stability strategy | grep for `frozen.snapshot\|prompt.stability\|cache.stability` returns 0 hits | Implicit stability (compact mode is stable) but not documented/enforced | 10-layer prompt assembly with cached vs. ephemeral separation |
| G7 | No formalized delegation protocol | "delegate" in scripts/ = functional delegation only (vault, backup) | Sub-agent handoff is ad-hoc, no context compression rules | `delegate_task`: isolated subagents with summary-only returns |
| G8 | No root-cause-before-retry | Connector retry logic exists but is mechanical (exponential backoff) | Blind retries waste tokens and API calls | Diagnose → hypothesize → different approach, then retry |

### What's Already Partially Covered

| Primitive | Existing Coverage | Remaining Gap |
|-----------|-------------------|---------------|
| Bounded memory (P2) | `fact_extractor.py` manages `state/memory.md` with schema v2.0 | Need: hard char cap, consolidation instructions in prompt, overflow handler |
| Session summarization (part of P5) | `session_summarizer.py` writes to `tmp/session_history_N.md` | Need: summaries searchable, not just disposable; search over `summaries/` dir |
| OODA analysis (part of P4) | `config/workflow/reason.md` Step 8 is full OODA | Need: add skills check as step 0 in OODA ("is there a learned procedure?") |
| Tiered eviction (part of P3) | `context_offloader.py` with `EvictionTier` enum | Need: pre-eviction memory flush step before compression fires |
| User modeling (part of P6) | `config/user_profile.yaml` with comprehensive schema | Need: AI self-model complement (what the agent knows about itself) |

---

## 4. Source Primitives — Hermes Agent + OpenClaw Extraction

Ten framework-agnostic primitives were distilled from Hermes Agent (`NousResearch/hermes-agent`, 7.5K★) and OpenClaw (314K★, Hermes's predecessor). Each primitive is proven in production. Only the pattern is adopted — zero framework code imported.

| ID | Primitive | Source | Pattern |
|:--:|-----------|--------|---------|
| P1 | Frozen Snapshot Injection | Hermes prompt assembly | Load bounded memory at session start. Never mutate mid-session. Cache stability > recency. |
| P2 | Capacitated Memory with Forced Consolidation | Hermes MEMORY.md (2,200 chars) | Hard char/token limits. When full, merge — don't append. The limit IS the discipline. |
| P3 | Pre-Eviction Memory Flush | Hermes pre-compression flush | Before context compression, give model one turn to persist facts. Then compress. |
| P4 | Procedural Memory (Skills-from-Experience) | Hermes skill_manage | After complex tasks (5+ steps, error recovery), extract working procedure as reusable skill. |
| P5 | Session Search (FTS5 + LLM Summarization) | Hermes session_search tool | Full-text search over session history. Pipe matches through cheap LLM for summarization. |
| P6 | Dual-Peer Self-Modeling | Hermes Honcho integration | AI builds a representation of itself — patterns, blind spots, effective strategies. |
| P7 | Isolated Subagent Delegation | Hermes delegate_task | Spawn subagents with own context. Only summary returns. Intermediate results never enter parent. |
| P8 | Zero-Context-Cost Tool Pipelines | Hermes execute_code (Python RPC) | Multi-step pipelines execute in script scope. Context sees only final result. |
| P9 | Prompt Stability Architecture | Hermes cached vs. ephemeral separation | Separate system prompt into stable (cached) and ephemeral (per-call) layers. |
| P10 | Cron in Fresh Sessions with Skill Backing | Hermes cronjob tool | Scheduled tasks run clean. Skills provide procedure. No inherited context. |

### Primitive-to-Phase Mapping

| Primitive | Phase | Effort Category |
|-----------|:-----:|:---------------:|
| P2 (Capacitated Memory) | AR-1 | Prompt-only |
| P6 (Self-Model) | AR-2 | State file + prompt |
| P3 (Pre-Eviction Flush) | AR-3 | Prompt + minor code |
| P5 (Session Search) | AR-4 | Code (~200 lines) |
| P4 (Procedural Memory) | AR-5 | State dir + prompt + code (~100 lines) |
| P1+P9 (Prompt Stability) | AR-6 | Prompt-only (audit) |
| P7+P8 (Delegation) | AR-7 | Prompt + code (~150 lines) |
| P10 (Root-Cause) | AR-8 | Prompt-only |

---

## 5. Execution Plan — Strangler Fig Phases

### Phase Sequencing

```
   AR-1 ─────► AR-3 ─────► AR-4 ─────► AR-5
 (memory cap)  (flush)    (search)  (procedures)
                                        │
   AR-2 ────────────────────────────────┘
 (self-model)    (independent, can start any time)

   AR-6 ─────► AR-7
 (stability)  (delegation)

   AR-8
 (root-cause)  (independent, prompt-only)
```

**Critical path**: AR-1 → AR-3 → AR-4 → AR-5 (memory → flush → search → procedures)
**Independent**: AR-2, AR-6, AR-8 (can ship in parallel with critical path)
**Depends on AR-6**: AR-7 (delegation needs stable prompt understanding)

---

### Phase AR-1: Bounded Memory & Consolidation Discipline

> **Primitive**: P2 (Capacitated Memory with Forced Consolidation)
> **Effort**: Prompt-only — zero code changes
> **Risk**: Low
> **Dependencies**: None (ships immediately)

#### Problem Statement

`state/memory.md` has a v2.0 schema with `facts: []` in YAML frontmatter, managed by `fact_extractor.py`. But there is no hard limit on the number of facts or total character count. As facts accumulate over weeks/months, `memory.md` will grow unbounded and eventually consume significant context when loaded via compact mode.

Hermes solves this with a 2,200-character cap on MEMORY.md. When full, the agent must **merge related entries and remove lowest-value facts** — it cannot simply append. The limit creates evolutionary pressure toward distilled, high-signal facts.

#### Existing Code (No Changes Needed)

- `scripts/fact_extractor.py` — `persist_facts()` writes to `state/memory.md`; `Fact` model has `confidence` field (0.0–1.0) and `ttl_days` for expiry
- `state/memory.md` — exists with schema v2.0, currently empty

#### Changes

**1. Add capacity constants to `fact_extractor.py`** (~5 lines):

```python
# Memory capacity limits (inspired by Hermes Agent bounded memory)
MAX_MEMORY_CHARS = 3_000    # Hard cap on facts section of state/memory.md
MAX_FACTS_COUNT = 30        # Absolute max individual facts
```

**2. Add overflow check to `persist_facts()`** (~20 lines):

```python
def persist_facts(facts: list[Fact], artha_dir: Path) -> int:
    """Append new facts. If over capacity, consolidate lowest-confidence entries."""
    existing = load_existing_facts(artha_dir)
    merged = deduplicate(existing + facts)

    # Capacity check
    serialized = serialize_facts(merged)
    if len(serialized) > MAX_MEMORY_CHARS or len(merged) > MAX_FACTS_COUNT:
        merged = _consolidate(merged)  # Drop expired, merge same-domain, evict lowest confidence

    write_memory_file(artha_dir, merged)
    return len(facts)
```

**3. Add consolidation instructions to `config/workflow/finalize.md`** (Step 11c addendum):

Add to the existing fact extraction step:

```markdown
#### Memory Capacity Protocol

After extracting facts, check `state/memory.md` total size:
- **If ≤ 30 facts and ≤ 3,000 characters**: append normally.
- **If approaching limit**: consolidate before appending:
  1. Remove facts where `ttl_days` has expired.
  2. Merge facts in the same domain with overlapping content.
  3. If still over capacity, evict lowest-confidence facts first.
- **Never exceed 30 facts or 3,000 characters.** The limit is the feature —
  it forces distillation of the most valuable knowledge.
```

#### Verification

- [ ] `state/memory.md` with 35 test facts → `persist_facts()` consolidates to ≤30
- [ ] Character overflow triggers consolidation, not truncation
- [ ] Expired facts (past TTL) are automatically pruned
- [ ] Existing 938 tests continue to pass

#### Config Flag

```yaml
harness:
  agentic:
    memory_capacity:
      enabled: true
      max_chars: 3000
      max_facts: 30
```

---

### Phase AR-2: Self-Model (AI Metacognition)

> **Primitive**: P6 (Dual-Peer Self-Modeling)
> **Effort**: New state file + prompt additions
> **Risk**: Low
> **Dependencies**: None (independent)

#### Problem Statement

`config/user_profile.yaml` gives Artha comprehensive understanding of the user — household type, integrations, emergency contacts, enabled domains. But the agent has zero awareness of **itself**: its own strengths, weaknesses, patterns, and effective strategies for this specific user.

Hermes solves this with dual-peer modeling: both user AND AI build representations. The AI's self-model is updated via `observe_me=True`, creating metacognitive awareness: "I tend to over-explain", "My finance analysis has been consistently accurate", "I missed immigration deadlines twice — be more cautious here."

#### Changes

**1. Create `state/self_model.md`** (new file, ~30 lines):

```markdown
---
domain: self_model
last_updated: never
schema_version: '1.0'
max_chars: 1500
---

## Artha Self-Model

Bounded self-awareness file. Updated at session boundaries (Step 11c),
never mid-turn. Frozen at session start for cache stability.

### Domain Confidence
<!-- Which domains am I consistently accurate in? Which do I struggle with? -->

### Effective Strategies
<!-- What approaches work well for this user? -->

### Known Blind Spots
<!-- Where have I made mistakes? What should I double-check? -->

### User Interaction Patterns
<!-- How does this user prefer to receive information? -->
```

**2. Add self-model injection to compact mode** (`generate_identity.py`):

In the prompt assembly, after user profile injection, add:

```markdown
### Self-Awareness

If `state/self_model.md` exists and is non-empty, read it at session start.
Use it to calibrate your confidence per domain:
- High-confidence domains: be decisive, summarize concisely
- Low-confidence domains: be explicit about uncertainty, suggest verification
- Known blind spots: double-check before asserting

Update `state/self_model.md` during Step 11c (fact extraction) when you
discover something about your own performance:
- A domain where you consistently get the right answer
- A domain where the user corrected you
- A strategy that worked particularly well
- A pattern in how the user prefers information

**Capacity**: 1,500 characters max. Consolidate if approaching limit.
```

**3. Add self-model update to finalize.md** (Step 11c extension):

```markdown
#### Self-Model Update (after fact extraction)

If during this session you discovered:
- A domain where you were notably accurate or inaccurate
- A user preference about communication style
- An effective strategy worth remembering
- A mistake worth noting as a blind spot

Then update `state/self_model.md` (resp. the relevant section).
Keep total content ≤ 1,500 characters. Merge/consolidate if needed.
Do NOT update every session — only when genuine insight was gained.
```

#### State Template

Add `state/templates/self_model.md` with the initial schema above.

#### Verification

- [ ] `state/self_model.md` created from template on first run
- [ ] Self-model loaded in compact mode (verify via `generate_identity.py` output)
- [ ] Self-model NOT loaded when file is empty/template-only (save tokens)
- [ ] Character cap of 1,500 enforced in prompt instructions
- [ ] Existing 938 tests continue to pass

#### Config Flag

```yaml
harness:
  agentic:
    self_model:
      enabled: true
      max_chars: 1500
```

---

### Phase AR-3: Pre-Eviction Memory Flush

> **Primitive**: P3 (Pre-Eviction Memory Flush)
> **Effort**: Prompt addition + minor code hook (~15 lines)
> **Risk**: Low
> **Dependencies**: AR-1 (bounded memory must exist for flush to write to)

#### Problem Statement

`session_summarizer.py` triggers at 70% context pressure (configurable). It compresses middle turns into a summary, preserving the first N and last N exchanges. But facts mentioned only in the compressed middle turns are **silently lost** — there's no opportunity for the agent to persist important information before compression fires.

Hermes solves this with a "pre-compression memory flush": before compression, the model gets one explicit turn to persist any facts from doomed turns. Then compression runs.

#### Existing Code

- `scripts/session_summarizer.py` — triggers at threshold, writes `tmp/session_history_N.md`
- `scripts/fact_extractor.py` — `extract_facts_from_summary()` runs in Step 11c
- `config/artha_config.yaml` — `session_summarization.threshold_pct: 70`

#### Changes

**1. Add pre-flush directive to session summarization prompt** (`config/Artha.core.md` § Session Protocol):

```markdown
### Pre-Compression Memory Flush

Before compressing session context (when context pressure crosses the
configured threshold, currently 70%):

1. **PAUSE** — do not compress yet.
2. **SCAN** middle turns that will be summarized. Identify any facts,
   user corrections, or important context that exists only in those turns.
3. **PERSIST** — if any valuable facts are found, extract them to
   `state/memory.md` using the normal fact extraction protocol.
   Also update `state/self_model.md` if relevant insights were gained.
4. **THEN COMPRESS** — proceed with session summarization.

This is a one-turn insurance policy. Without it, facts mentioned only
in middle turns are lost when those turns are summarized away.
```

**2. Add `flush_before_compress` flag to `session_summarizer.py`** (~15 lines):

```python
def should_flush_memory(ctx: ArthaContext | None) -> bool:
    """Return True if we should give the model a chance to persist
    facts before compressing. Checks config flag."""
    if ctx is None:
        return False
    config = _load_config(Path(ctx.artha_dir))
    return config.get("harness", {}).get("agentic", {}).get(
        "pre_eviction_flush", {}).get("enabled", True)
```

**3. Emit a marker in the compression summary when flush occurred**:

In `SessionSummary`, add optional field:

```python
pre_flush_facts_persisted: int = 0  # Count of facts saved before compression
```

#### Verification

- [ ] When context hits 70%, flush directive fires before compression
- [ ] Facts from middle turns are persisted to `state/memory.md`
- [ ] Compression proceeds normally after flush
- [ ] `pre_flush_facts_persisted` count appears in summary metadata
- [ ] Existing 938 tests continue to pass
- [ ] New test: mock 70% pressure → verify flush called before compress

#### Config Flag

```yaml
harness:
  agentic:
    pre_eviction_flush:
      enabled: true
```

---

### Phase AR-4: Session Search & Cross-Session Recall

> **Primitive**: P5 (Session Search — FTS5 + LLM Summarization)
> **Effort**: New module (~200 lines) + prompt additions
> **Risk**: Medium (new capability, but read-only — no state mutation risk)
> **Dependencies**: DA-3 (session_summarizer.py must exist — ✅ implemented)

#### Problem Statement

Artha produces briefings in `briefings/` (daily catch-up outputs) and session summaries in `tmp/session_history_N.md`. These are write-only archives — never searched or referenced. When the user says "remember that immigration issue from last week?" the agent has no recall mechanism.

Hermes solves this with `session_search`: FTS5 full-text search over a SQLite session store + Gemini Flash summarization for human-readable results. No vector database. No embedding infrastructure.

#### Design Decision: grep vs. SQLite FTS5

| Option | Pros | Cons |
|--------|------|------|
| **A: grep over files** | Zero infrastructure, uses existing `briefings/*.md` files | Slow for large histories, no ranking, regex-only matching |
| **B: SQLite FTS5** | Fast full-text search, relevance ranking, proven pattern | New dependency (sqlite3 is stdlib), new DB file to manage |

**Decision**: **Option A (grep over files)** for Phase AR-4. Rationale:
- Artha has ~10 briefings today. Even at daily cadence, that's ~365 files/year — grep handles this comfortably.
- Aligns with "state in Markdown" philosophy (Tech Spec §1.1 principle 3).
- No new DB file to backup/migrate/encrypt.
- Can upgrade to SQLite FTS5 later if file count exceeds ~1,000.

#### Changes

**1. Create `scripts/session_search.py`** (~200 lines):

```python
"""Search historical briefings and session summaries for cross-session recall.

Usage:
    from session_search import search_sessions

    results = search_sessions(
        query="immigration deadline extension",
        artha_dir=Path("..."),
        max_results=5,
    )
    # Returns list of SearchResult(file, date, excerpt, relevance_score)
"""

@dataclass
class SearchResult:
    file: Path
    date: str           # Extracted from filename (YYYY-MM-DD)
    excerpt: str        # Surrounding context lines (~200 chars)
    match_count: int    # Number of query term matches in file

def search_sessions(
    query: str,
    artha_dir: Path,
    max_results: int = 5,
    *,
    search_dirs: tuple[str, ...] = ("briefings", "summaries"),
) -> list[SearchResult]:
    """Full-text search over briefings/ and summaries/ directories.

    Splits query into terms, searches each .md file, ranks by
    match density (matches / file_length), returns top N results
    with surrounding context excerpts.
    """
```

**2. Add search capability to reasoning prompt** (`config/workflow/reason.md` addendum):

```markdown
### Cross-Session Recall (Step 8, pre-OODA)

Before running the OODA protocol, check if the user's request references
past sessions ("we discussed", "last time", "remember when", "previously"):

If yes, search `briefings/` and `summaries/` for relevant context:
1. Extract key terms from the user's reference.
2. Search historical files for those terms.
3. Include the top 3 relevant excerpts as context for the OODA analysis.
4. Cite the source date: "Per your March 8 briefing, ..."

This transforms session history from passive archive into active
diagnostic resource. Use it proactively when debugging recurring issues —
"Has this problem appeared in prior briefings?"
```

**3. Add search to ArthaContext** (optional signal):

```python
# In artha_context.py
class ArthaContext(BaseModel):
    # ... existing fields ...
    session_recall_available: bool = False  # True if briefings/ has ≥1 file
```

#### Verification

- [ ] `search_sessions("immigration")` returns matching briefings with excerpts
- [ ] Results ranked by match density (not just first-found)
- [ ] Empty query returns empty list (no error)
- [ ] Missing directories handled gracefully (no crash)
- [ ] Results exclude PII (respect existing PII guard patterns)
- [ ] New tests: 10+ covering search, ranking, edge cases
- [ ] Existing 938 tests continue to pass

#### Config Flag

```yaml
harness:
  agentic:
    session_search:
      enabled: true
      search_dirs: [briefings, summaries]
      max_results: 5
```

---

### Phase AR-5: Procedural Memory (Learned Skills)

> **Primitive**: P4 (Skills-from-Experience)
> **Effort**: New directory + prompt additions + minor code (~100 lines)
> **Risk**: Medium (new knowledge type, but read-only retrieval — writes are prompt-gated)
> **Dependencies**: AR-2 (self-model provides the "should I save?" calibration), AR-4 (search enables "did I already learn this?")

#### Problem Statement

`config/skills.yaml` defines 12 data extraction skills (USCIS, weather, property tax, etc.). These are **author-curated** — the user explicitly defined each skill. The agent has no mechanism to create skills from its own experience.

Hermes solves this with `skill_manage`: after completing a complex task (5+ tool calls, error recovery, user corrections), the agent extracts the working procedure as a reusable skill. Next time a similar task appears, it follows the learned procedure instead of re-discovering the solution.

#### Design Decision: Where to Store Learned Procedures

| Option | Location | Pros | Cons |
|--------|----------|------|------|
| A | `config/skills.yaml` (extend existing) | Single skills registry | Mixes curated and learned; schema collision |
| B | `state/learned_procedures/` (new dir) | Clean separation; state = agent-owned | New directory to manage |
| C | `state/memory.md` (extend facts) | No new files | Memory is for facts, not procedures; capacity conflict |

**Decision**: **Option B** — `state/learned_procedures/`. Rationale:
- Clean DDD boundary: `config/` = human-curated, `state/` = agent-generated
- Each procedure is a self-contained Markdown file (aligns with "state in Markdown")
- `domain_index.py` can enumerate procedures when relevant domain is loaded
- No capacity conflict with `state/memory.md` (separate concern)

#### Changes

**1. Create `state/learned_procedures/` directory** with README:

```markdown
# Learned Procedures

This directory stores procedures Artha learned from experience.
Each file is a Markdown document describing a working approach
for a non-trivial task.

Files are created automatically during Step 11c when a task:
- Required 5+ tool calls to complete
- Involved error recovery or dead ends
- Resulted in the user correcting the initial approach

Format:
  {domain}-{slug}.md

Do NOT manually edit — managed by Artha's fact extraction pipeline.
```

**2. Define procedure Markdown format**:

```markdown
---
domain: immigration
created: 2026-03-15
source: session-2026-03-15
trigger: "USCIS status check when case number format changed"
confidence: 0.9
---

## Procedure: USCIS Status Check with New Case Number Format

### When to Use
- User asks about immigration case status
- Case number starts with IOE (new format) instead of MSC (legacy)

### Steps
1. Use `scripts/skills/uscis_status.py` with the IOE prefix
2. If 403 error: check IP against USCIS geo-restriction list
3. Fall back to web scraping if API blocked
4. Cross-reference with visa bulletin for priority date

### Pitfalls
- IOE case numbers require different URL path than MSC
- USCIS rate-limits to 5 requests per minute from same IP

### Verification
- Status response contains "Case Was Received" or similar known statuses
```

**3. Add procedure extraction trigger to `config/workflow/finalize.md`** (Step 11c extension):

```markdown
#### Procedure Extraction (after fact extraction and self-model update)

If during this session:
- You completed a task requiring 5+ distinct tool calls or file operations
- You encountered errors and found the working path through trial
- The user corrected your approach and the correction led to success
- You discovered a workflow that wasn't obvious from the instructions

Then extract the working procedure:
1. Search `state/learned_procedures/` — does a similar procedure already exist?
   - If yes, patch the existing file (update steps, add pitfalls)
   - If no, create a new file: `state/learned_procedures/{domain}-{slug}.md`
2. Format: trigger (when to use), steps (what to do), pitfalls, verification
3. Keep each procedure ≤ 1,500 characters. Concise > comprehensive.

Do NOT create a procedure for simple, straightforward tasks.
The threshold is: "Would I benefit from having this written down next time?"
```

**4. Add procedure lookup to OODA** (`config/workflow/reason.md` Step 8 pre-OODA):

```markdown
#### Procedure Lookup (before OODA analysis)

Before generating a novel approach:
1. Scan `state/learned_procedures/` for procedures matching the current task domain.
2. If a matching procedure exists with confidence ≥ 0.7:
   - Follow it as the primary approach, adapting to current context.
   - Skip OODA orient/decide for that specific sub-task (procedure IS the decision).
3. If no procedure matches: proceed with full OODA analysis.

Procedure-first execution compounds intelligence — each session builds
on prior sessions instead of starting from zero.
```

**5. Create `scripts/procedure_index.py`** (~100 lines):

```python
"""Index and search learned procedures in state/learned_procedures/.

Usage:
    from procedure_index import find_matching_procedures

    matches = find_matching_procedures(
        query="USCIS status check",
        artha_dir=Path("..."),
    )
"""

@dataclass
class ProcedureMatch:
    file: Path
    domain: str
    trigger: str
    confidence: float
    relevance: float  # 0.0–1.0 based on query match

def find_matching_procedures(
    query: str,
    artha_dir: Path,
    min_confidence: float = 0.7,
) -> list[ProcedureMatch]:
    """Scan learned_procedures/*.md frontmatter, match against query."""
```

#### Verification

- [ ] After a 5+-step task, procedure file created in `state/learned_procedures/`
- [ ] Procedure found on subsequent session via `find_matching_procedures()`
- [ ] Procedure-first execution skips redundant OODA for known tasks
- [ ] Existing procedures patchable (not just append-only)
- [ ] Directory listing works when empty (no crash)
- [ ] New tests: 8+ covering creation, lookup, matching, patching
- [ ] Existing 938 tests continue to pass

#### Config Flag

```yaml
harness:
  agentic:
    procedural_memory:
      enabled: true
      min_steps_to_trigger: 5
      max_procedure_chars: 1500
```

#### Backup Integration

Add `state/learned_procedures/` to the backup manifest (`scripts/backup.py` registry):

```python
# In BACKUP_REGISTRY
"learned_procedures": {
    "path": "state/learned_procedures/",
    "type": "directory",
    "encrypted": False,  # Procedures are not PII-sensitive
},
```

---

### Phase AR-6: Prompt Stability Architecture

> **Primitive**: P1 + P9 (Frozen Snapshot Injection + Prompt Stability)
> **Effort**: Prompt-only — audit and documentation, zero code changes
> **Risk**: Very low
> **Dependencies**: None (ships immediately)

#### Problem Statement

Artha's `generate_identity.py` already produces a stable compact mode instruction file (~15KB). But the stability is **implicit** — it happens to be stable because the generation script runs once. There's no documented architecture for WHY stability matters, what constitutes the "frozen" layer vs. "ephemeral" layer, or what rules govern mid-session mutations.

Hermes Agent makes this explicit: 10-layer prompt assembly with clear cached/ephemeral separation. System prompt assembled once at session start. Memory writes update disk but never mutate the built system prompt. Ephemeral context (Honcho recall, domain excerpts) injected per-API-call and never persisted.

#### What Already Works

- `generate_identity.py` runs once → produces `config/Artha.md` (stable across session)
- `config/workflow/*.md` files loaded per-phase (stable within phase)
- Domain prompts loaded on-demand via `domain_index.py` (already ephemeral — loaded per-command)
- `state/memory.md` loaded via fact extraction (currently not injected into system prompt — facts are used by the agent when relevant)

#### Changes

**1. Document the stability architecture in `config/Artha.core.md`** (new section):

```markdown
### Prompt Stability Architecture

Artha's instruction file (`config/Artha.md`) is the system prompt. Its
stability directly affects cost and performance:

**Why stability matters**: LLM providers (Anthropic, OpenAI) cache
the system prompt prefix. A stable prefix = 90% input token cost reduction
on subsequent turns. Every mutation resets the cache (Anthropic: 5-min TTL).

**Frozen layer** (stable across entire session — never mutate mid-session):
- `config/Artha.md` — the core instruction file
- `config/user_profile.yaml` — user context (loaded once at session start)
- `state/memory.md` — persistent facts (loaded once, writes update disk only)
- `state/self_model.md` — AI self-awareness (loaded once, writes update disk only)

**Ephemeral layer** (per-command, injected dynamically, never persisted in prompt):
- Domain prompts from `prompts/` (loaded via `domain_index.py` per-command)
- Domain state from `state/` (loaded per-command)
- Session history summaries (from `tmp/session_history_N.md`)
- Cross-session search results (from `session_search.py`)
- Learned procedure content (from `state/learned_procedures/`)
- Pipeline output / email data / calendar events

**Rules**:
1. NEVER modify `config/Artha.md` mid-session.
2. Disk writes to `state/memory.md`, `state/self_model.md` update for the
   NEXT session but do NOT trigger instruction reloads in the current session.
3. Domain context is ephemeral — it enters context when needed and is
   eligible for eviction by `context_offloader.py`.
4. When adding new context sources, classify them as frozen or ephemeral
   BEFORE implementation. Default to ephemeral unless truly session-stable.
```

**2. Add stability marker to `generate_identity.py` output**:

Add a comment block at the top of generated `config/Artha.md`:

```markdown
<!-- PROMPT STABILITY: This file is the frozen system prompt layer.
     Do NOT modify mid-session. Changes take effect on next session start.
     See config/Artha.core.md § Prompt Stability Architecture. -->
```

#### Verification

- [ ] `config/Artha.md` contains stability marker comment
- [ ] `config/Artha.core.md` contains Prompt Stability Architecture section
- [ ] All context sources classified as frozen or ephemeral
- [ ] No mid-session instruction reload codepaths exist
- [ ] Existing 938 tests continue to pass

#### Config Flag

None needed — documentation-only change.

---

### Phase AR-7: Delegation Protocol Formalization

> **Primitive**: P7 + P8 (Isolated Subagent Delegation + Zero-Context Pipelines)
> **Effort**: Prompt additions + code scaffold (~150 lines)
> **Risk**: Medium (depends on IDE sub-agent API stability — same blocker as AI-6/DA-7)
> **Dependencies**: AR-6 (prompt stability — delegation must understand what's frozen vs. ephemeral), AI-3 (ArthaContext provides handoff data — ✅ implemented)

#### Relationship to agentic-improve.md Phase 6

agentic-improve.md Phase 6 ("Sub-Agent Context Isolation with Handoff Compression") was **deferred** pending IDE sub-agent API stabilization. This phase **supersedes AI-6** with an updated design informed by Hermes Agent's `delegate_task` pattern. Key differences:

| Aspect | AI-6 (Original) | AR-7 (Updated) |
|--------|-----------------|-----------------|
| Handoff format | JSON with structured return contract | Summary-only text return (simpler, more robust) |
| Subagent scope | Per-domain isolation | Task-based isolation (may span domains) |
| Budget tracking | Not specified | Explicit iteration budget with pressure hints |
| Procedure extraction | Not included | Post-delegation skill creation trigger |
| Fallback | `PromptSimulatedRunner` | Prompt-mode fallback (no code needed) |
| Pre-condition | IDE API stable for 2+ releases | IDE API stable for 1+ release OR prompt-mode |

#### Changes

**1. Define delegation protocol in `config/Artha.core.md`** (new section):

```markdown
### Delegation Protocol

When a task within the catch-up workflow or an ad-hoc request meets
the delegation criteria, spawn an isolated sub-agent:

**Delegation criteria** (any of):
- Task requires 5+ anticipated tool calls or file reads
- Task is embarrassingly parallel (independent data gathering)
- Task operates on an isolated domain with no cross-domain dependencies

**Handoff composition**:
1. Task description (what to accomplish, not how)
2. Minimal context excerpt (NOT full conversation — extract only relevant bits)
3. Budget: "Complete this in ≤N tool calls. Return best partial result if limit reached."
4. Output format: "Return a concise summary (≤500 chars). Do not include raw data."

**Agent selection**:
- Read-only research → `Explore` agent (safe, parallel-friendly)
- State mutations required → default agent

**Post-delegation**:
- Receive summary-only result
- If task was complex (5+ steps): evaluate for procedure extraction (AR-5)
- Never import the subagent's full transcript into parent context

**Prompt-mode fallback** (when sub-agent API unavailable):
Use prompt markers to simulate isolation:
```
--- DELEGATED TASK START ---
Task: [description]
Context: [minimal excerpt]
Budget: [N tool calls max]
--- DELEGATED TASK END ---
Result: [summary only]
```
```

**2. Create `scripts/delegation.py`** (~150 lines):

```python
"""Delegation protocol for Artha sub-agent spawning.

Provides handoff composition and budget tracking utilities.
Does NOT directly invoke sub-agents (that's the IDE's job) —
instead, formats the delegation request and validates the response.
"""

@dataclass
class DelegationRequest:
    task: str
    context_excerpt: str   # Minimal context (NOT full conversation)
    budget: int            # Max tool calls
    agent: str = "Explore" # or "default"
    output_max_chars: int = 500

@dataclass
class DelegationResult:
    summary: str
    tool_calls_used: int | None = None
    procedure_candidate: bool = False  # True if 5+ steps or error recovery

def compose_handoff(
    task: str,
    ctx: ArthaContext,
    *,
    relevant_state: dict[str, str] | None = None,
    budget: int = 10,
) -> DelegationRequest:
    """Build a compressed handoff for sub-agent delegation.

    Extracts only the context the subagent needs:
    - User identity (from user_profile)
    - Current command and date
    - Domain-specific state excerpt (if relevant)
    - Active corrections from state/memory.md (if domain-matched)
    """

def should_delegate(estimated_steps: int, is_parallel: bool, is_isolated: bool) -> bool:
    """Return True if task meets delegation criteria."""
    return estimated_steps >= 5 or is_parallel or is_isolated
```

#### Verification

- [ ] `compose_handoff()` produces context < 2K tokens for single-domain tasks
- [ ] Budget is communicated to subagent in handoff text
- [ ] Summary-only return enforced (subagent transcript never enters parent)
- [ ] Prompt-mode fallback works when sub-agent API unavailable
- [ ] Post-delegation procedure extraction triggers for 5+ step tasks
- [ ] New tests: 8+ covering handoff composition, budget, fallback
- [ ] Existing 938 tests continue to pass

#### Config Flag

```yaml
harness:
  agentic:
    delegation:
      enabled: true
      default_budget: 10
      max_budget: 20
      fallback_mode: prompt  # "prompt" or "subagent"
```

#### Gating Condition

This phase ships in **prompt-mode** immediately (delegation protocol documented + respected by the agent). The `subagent` mode activates when IDE sub-agent API has been stable for 1+ release cycle in the user's primary IDE.

---

### Phase AR-8: Root-Cause Before Retry Protocol

> **Primitive**: Hermes root-cause-before-retry pattern
> **Effort**: Prompt-only — zero code changes
> **Risk**: Very low
> **Dependencies**: None (ships immediately)

#### Problem Statement

Artha's connectors have retry logic (`lib/retry.py` — exponential backoff). Pipeline errors are logged and surfaced. But the agent's response to tool failures during the catch-up workflow is not systematically structured — it may retry the same approach blindly, especially for novel failures outside the connector layer.

Hermes's approach: when a tool call fails, the agent MUST diagnose WHY before attempting again. This is enforced through the prompt, not code.

#### Changes

**1. Add root-cause protocol to `config/Artha.core.md`** (new section):

```markdown
### Root-Cause Before Retry

When any operation fails during a workflow step:

1. **READ** the error message completely. Do not skip details.
2. **DIAGNOSE** — form a hypothesis about the root cause:
   - Is it a transient issue (network, rate limit, timeout)?
   - Is it a configuration issue (missing credential, wrong path)?
   - Is it a logic error (wrong input format, unexpected state)?
   - Is it an environmental issue (permission, missing dependency)?
3. **DECIDE** — based on diagnosis:
   - **Transient**: retry once with backoff. If still failing, report.
   - **Configuration**: report the specific misconfiguration. Do NOT retry.
   - **Logic**: try a DIFFERENT approach. Do NOT retry the same call.
   - **Environmental**: report and suggest remediation. Do NOT retry.
4. **NEVER** retry the same failing operation more than once without
   changing something (input, approach, or context).

**Anti-pattern** (do NOT do this):
```
tool_call → error → retry same call → error → retry again → error → give up
```

**Correct pattern**:
```
tool_call → error → diagnose cause → different approach → success
```

When the root-cause protocol resolves an issue through a non-obvious path,
this is a strong signal for procedure extraction (AR-5).
```

**2. Add root-cause checkpoint to `config/workflow/fetch.md`** (Step 3 failure handling):

```markdown
#### Connector Failure Handling (Step 3)

If a connector fails during pipeline fetch:
1. Check the error type against `lib/retry.py` known-transient list.
2. If transient AND first failure: automatic retry (already handled by pipeline).
3. If NOT transient OR second failure: apply Root-Cause Before Retry protocol.
4. Log diagnosis to `state/audit.md` with: connector, error, diagnosis, action taken.
5. Continue with remaining connectors (partial success = exit code 3).
```

#### Verification

- [ ] Root-cause protocol documented in `config/Artha.core.md`
- [ ] Fetch phase references root-cause protocol on connector failure
- [ ] No blind retry loops in prompt instructions
- [ ] Existing 938 tests continue to pass

#### Config Flag

None needed — prompt-only behavioral change.

---

## 6. Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|:--:|------|:----------:|:------:|------------|
| R1 | Memory consolidation drops high-value facts | Low | High | Confidence-ordered eviction (lowest confidence first). TTL expiry checked before consolidation. User corrections (confidence=1.0) are never auto-evicted. |
| R2 | Self-model creates overconfidence bias | Medium | Medium | Self-model includes "Known Blind Spots" section. Instructions emphasize uncertainty calibration. Self-model is advisory — never overrides explicit user instructions. |
| R3 | Pre-eviction flush adds latency to compression | Low | Low | Flush is one turn (~5s). Compression already takes 10–30s. Marginal addition. Can be disabled via config flag. |
| R4 | Session search returns PII from old briefings | Medium | High | Search excerpts pass through existing PII guard regex. Briefings in `briefings/` already go through PII redaction at creation time. Belt and suspenders. |
| R5 | Learned procedures become stale | Medium | Medium | Each procedure has `confidence` field + `created` date. Procedures older than 90 days with no re-validation get confidence decayed (0.9 → 0.7 → 0.5). Low-confidence procedures are deprioritized in matching. |
| R6 | Delegation handoff leaks sensitive context | Low | High | Handoff composition uses PII middleware before sending. Handoff explicitly excludes full state files — only frontmatter/summaries. Budget cap limits subagent's scope. |
| R7 | Prompt stability rules are violated by future changes | Medium | Medium | Stability marker comment in `config/Artha.md`. Classification rule in `Artha.core.md`. Code review checklist item: "Is new context frozen or ephemeral?" |
| R8 | Procedure extraction creates too many files | Low | Low | 5-step minimum threshold filters trivial tasks. 1,500 char cap per procedure. Directory listing in `domain_index.py` prevents unbounded growth discovery. Max procedure count: 50 (configurable). |
| R9 | Root-cause protocol slows down routine operations | Very Low | Low | Protocol only activates on FAILURE — happy path unchanged. Transient failures still auto-retry via existing `lib/retry.py`. |
| R10 | Config flag proliferation | Medium | Low | All AR-* flags nested under `harness.agentic.*`. Single kill-switch: `harness.agentic.enabled: false` disables all. Follows established pattern from DA-1–5 and AI-1–5. |

---

## 7. Trade-Off Decision Log

| ID | Decision | Chosen | Rejected | Rationale |
|:--:|----------|--------|----------|-----------|
| T1 | Memory cap: chars vs. facts count | **Both** (3,000 chars AND 30 facts) | Single limit | Char cap prevents verbose facts from consuming all space. Count cap prevents fragmentation into many tiny facts. Dual limit covers both failure modes. |
| T2 | Self-model location | **state/self_model.md** | Extend user_profile.yaml | DDD: user profile is human-authored, self-model is agent-authored. Different owners = different files. Also different capacity rules. |
| T3 | Session search: grep vs. FTS5 | **grep** (Phase 1) | SQLite FTS5 | ~365 files/year, grep handles comfortably. "State in Markdown" philosophy. Upgrade path to FTS5 preserved via `SearchResult` abstraction. |
| T4 | Procedure storage: extend skills.yaml vs. new dir | **state/learned_procedures/** | config/skills.yaml | Clean boundary: config/ = human, state/ = agent. Schema differences (data skills vs. procedural skills). No conflict with existing `skill_runner.py`. |
| T5 | Delegation: structured JSON vs. text | **Text (summary-only)** | JSON contract | Simpler, more robust across IDE API variations. JSON contract requires schema enforcement on subagent output. Text summary is universally parseable. |
| T6 | Pre-eviction: automatic vs. prompted | **Prompted** (agent gets directive) | Automatic extraction | Respects "prompts are logic layer" principle. Agent chooses what to persist based on context. Automatic extraction can't evaluate semantic importance. |
| T7 | Self-model update frequency | **Session boundaries only** | Per-turn updates | Frozen snapshot pattern: mid-session mutations invalidate cache stability (AR-6). Session-boundary writes update disk for next session. |
| T8 | Prompt stability: enforce vs. document | **Document** (for now) | Code enforcement | Enforcement would require intercepting system prompt mutations — too invasive for the current architecture. Documentation + code review is sufficient. Enforcement is a future Phase if violations become a pattern. |

---

## 8. Dependency Map & Critical Path

```
                    ┌─────────────────────┐
                    │ IMMEDIATE (prompt)   │
                    ├─────────────────────┤
                    │ AR-1: Memory cap     │──────┐
                    │ AR-6: Prompt stability│      │
                    │ AR-8: Root-cause     │      │
                    └─────────────────────┘      │
                                                  │
                    ┌─────────────────────┐      │
                    │ WEEK 1 (prompt+file) │      │
                    ├─────────────────────┤      │
                    │ AR-2: Self-model     │      │ (depends on AR-1)
                    │ AR-3: Pre-eviction   │◄─────┘
                    └─────────────────────┘
                              │
                              │ (depends on AR-3)
                    ┌─────────────────────┐
                    │ WEEK 2 (code ~200L)  │
                    ├─────────────────────┤
                    │ AR-4: Session search │
                    └─────────────────────┘
                              │
                              │ (depends on AR-4 + AR-2)
                    ┌─────────────────────┐
                    │ WEEK 3 (code ~100L)  │
                    ├─────────────────────┤
                    │ AR-5: Procedures     │
                    └─────────────────────┘

                    ┌─────────────────────┐
                    │ WEEK 3+ (gated)      │
                    ├─────────────────────┤
                    │ AR-7: Delegation     │ (depends on AR-6 + IDE API)
                    └─────────────────────┘
```

### Critical Path

**AR-1 → AR-3 → AR-4 → AR-5**: Memory cap → flush → search → procedures. This is the learning loop: facts persist within capacity (AR-1), survive compression (AR-3), are searchable across sessions (AR-4), and compound into reusable procedures (AR-5).

### Independent Tracks

- **AR-2** (self-model): Can ship immediately alongside AR-1. No code dependencies.
- **AR-6** (prompt stability): Prompt-only documentation. Ship immediately.
- **AR-8** (root-cause): Prompt-only behavioral change. Ship immediately.
- **AR-7** (delegation): Gated on IDE API stabilization. Prompt-mode ships immediately; subagent-mode when ready.

---

## 9. Backward Compatibility Contract

| Phase | Backward Compatible | Migration |
|:-----:|:-------------------:|-----------|
| AR-1 | ✅ Yes | Existing empty `state/memory.md` gains capacity enforcement. No data loss — only affects future writes. Config flag opt-out. |
| AR-2 | ✅ Yes | New file `state/self_model.md`. Does not exist today — no migration. Empty file = no tokens consumed. |
| AR-3 | ✅ Yes | Adds pre-flush step to existing compression. Compression still works without flush (flush is advisory). Config flag opt-out. |
| AR-4 | ✅ Yes | New module `scripts/session_search.py`. New capability — no existing behavior changed. Config flag opt-out. |
| AR-5 | ✅ Yes | New directory `state/learned_procedures/`. Empty = no behavior change. Lookup returns empty results. Config flag opt-out. |
| AR-6 | ✅ Yes | Documentation only. No code changes. No behavior changes. |
| AR-7 | ✅ Yes | New module `scripts/delegation.py`. Prompt-mode fallback = behavior change is advisory in prompt only. Config flag opt-out. |
| AR-8 | ✅ Yes | Prompt additions only. No code changes. Agent behavior improves but is never breaking. |

### Zero-Downtime Guarantee

Every phase is fully backward compatible. All new features are behind config flags under `harness.agentic.*` with `enabled: true` defaults (opt-out model, following established pattern from DA-1–5 and AI-1–5).

No existing tests will break. Each phase adds tests — never modifies existing test assertions.

---

## 10. Observability Strategy

### Metrics (reported in Step 16 `harness_metrics`)

```yaml
harness_metrics:
  # Existing metrics (DA-1–5, AI-1–5) ...

  # AR-1: Memory capacity
  memory_capacity:
    total_facts: 12
    total_chars: 1847
    capacity_pct: 61.6  # 1847/3000
    consolidations_triggered: 0

  # AR-2: Self-model
  self_model:
    loaded: true
    chars: 450
    last_updated: "2026-03-14"

  # AR-3: Pre-eviction flush
  pre_eviction_flush:
    triggered: false      # Only true when compression fires
    facts_rescued: 0      # Facts persisted during flush

  # AR-4: Session search
  session_search:
    queries_made: 1
    results_returned: 3
    search_dirs_scanned: [briefings, summaries]

  # AR-5: Procedural memory
  procedural_memory:
    procedures_available: 4
    procedures_matched: 1  # Procedures used this session
    procedures_created: 0  # New procedures saved this session

  # AR-7: Delegation
  delegation:
    tasks_delegated: 0
    mode: prompt           # or "subagent"
    budget_used: 0
    budget_allocated: 0
```

### Audit Trail Extension

New events logged to `state/audit.md`:

| Event | Severity | Data |
|-------|:--------:|------|
| `MEMORY_CONSOLIDATION` | INFO | facts_before, facts_after, evicted_ids |
| `SELF_MODEL_UPDATE` | INFO | section_updated, chars_delta |
| `PRE_EVICTION_FLUSH` | INFO | facts_rescued_count, domains |
| `SESSION_SEARCH` | INFO | query, results_count, search_dirs |
| `PROCEDURE_CREATED` | INFO | domain, trigger, file_path |
| `PROCEDURE_MATCHED` | INFO | domain, procedure_file, confidence |
| `DELEGATION_SPAWNED` | INFO | task_summary, budget, agent_type |
| `ROOT_CAUSE_DIAGNOSIS` | WARN | error, diagnosis, action_taken |

### Compliance Audit Extension

Add to `scripts/audit_compliance.py`:

```python
def check_memory_capacity(memory_path: Path) -> CheckResult:
    """Verify memory.md is within capacity limits."""
    # Weight: 5 points
    # Pass: ≤ 30 facts AND ≤ 3,000 chars
    # Warn: 80–100% capacity
    # Fail: over capacity

def check_prompt_stability(artha_md_path: Path) -> CheckResult:
    """Verify Artha.md has stability marker and was not modified mid-session."""
    # Weight: 3 points
    # Pass: stability marker comment present
    # Fail: marker missing
```

---

## 11. Success Criteria

### Phase-Level Acceptance

| Phase | Success Criterion | Measurement |
|:-----:|-------------------|-------------|
| AR-1 | Memory stays within 3,000 chars after 30+ days of daily use | Monthly capacity check in compliance audit |
| AR-2 | Self-model contains ≥ 3 entries after 10 sessions | Manual review of `state/self_model.md` |
| AR-3 | Zero facts lost during compression (vs. current: unknown loss rate) | Compare pre-/post-compression fact counts in audit trail |
| AR-4 | Cross-session references resolved in ≥ 80% of cases | Manual review of catch-up sessions referencing past briefings |
| AR-5 | ≥ 3 learned procedures created after 20 sessions | Directory listing of `state/learned_procedures/` |
| AR-6 | System prompt unchanged mid-session in 100% of sessions | Compliance audit check |
| AR-7 | Delegated tasks return ≤ 500 char summaries (no context leakage) | Audit trail `DELEGATION_SPAWNED` events |
| AR-8 | Zero blind retries in catch-up workflow | Audit trail `ROOT_CAUSE_DIAGNOSIS` events |

### Aggregate Success (All Phases)

| Metric | Baseline (v7.0.1) | Target (post-AR-8) |
|--------|:------------------:|:-------------------:|
| Test count | 938 | ≥ 980 (+42 new) |
| Modules | 15 | 18 (+3 new: session_search, procedure_index, delegation) |
| Memory capacity utilization | Unbounded | ≤ 100% of 3,000 char cap |
| Cross-session recall | None | Available (grep-based) |
| Learned procedures | 0 | 3+ after 20 sessions |
| Self-model entries | 0 | 3+ after 10 sessions |

---

## 12. Assumptions & Constraints

### Assumptions

1. **Daily cadence**: User runs catch-up 1–2x daily. ~365 briefings/year is manageable for grep-based search.
2. **Markdown state files**: All new state (self_model.md, learned_procedures/*.md) follows existing "state in Markdown" convention.
3. **Prompt-driven logic**: All behavioral changes (root-cause, procedure lookup, delegation rules) are prompt instructions, not code enforcement. Code only provides data (search, index, compose).
4. **LLM provider caching**: Anthropic prompt caching (5-min TTL, 90% cost reduction) is the primary economic driver for prompt stability.
5. **Sub-agent API**: IDE sub-agent API (VS Code `runSubagent`) will stabilize within 1–2 release cycles. Prompt-mode fallback covers the gap.

### Constraints

1. **Zero external dependencies**: No new pip packages. All new code uses stdlib (pathlib, dataclasses, json, re).
2. **Pydantic optional**: New Pydantic models follow existing pattern — graceful fallback if Pydantic unavailable (see `session_summarizer.py` `_PYDANTIC_AVAILABLE` flag).
3. **Config flag consistency**: All flags under `harness.agentic.*` with `enabled: true` default. Single kill-switch at `harness.agentic.enabled: false`.
4. **Backward compatibility**: Every phase ships with zero breaking changes. Existing 938 tests must continue to pass.
5. **File count discipline**: ≤ 3 new Python modules. ≤ 2 new state files. ≤ 1 new state directory. Follows "avoid file bloat" principle.

### Relationship to Deferred Phases

| Deferred Phase | Original Spec | Status in AR |
|---------------|---------------|:-------------|
| DA-6 (State Abstraction Layer) | deep-agents.md | **Still deferred.** AR phases work with existing state file model. If DA-6 is implemented later, AR changes migrate naturally (they're all in state/ and config/). |
| DA-7 (Sub-Task Context Isolation) | deep-agents.md | **Superseded by AR-7.** AR-7 provides a more complete delegation protocol with prompt-mode fallback, budget tracking, and procedure extraction. |
| AI-6 (Sub-Agent Isolation with Handoff Compression) | agentic-improve.md | **Superseded by AR-7.** AR-7 updates the AI-6 design with Hermes Agent patterns (summary-only returns, budget, procedure extraction). Same intent, better design. |

---

## Appendix A: Primitive Source Attribution

| Primitive | Hermes Agent Component | Documentation URL | Lines Analyzed |
|-----------|----------------------|-------------------|:--------------:|
| P1 (Frozen Snapshot) | `agent/prompt_builder.py`, Memory docs | hermes-agent.nousresearch.com/docs/developer-guide/prompt-assembly | ~500 |
| P2 (Capacitated Memory) | `tools/memory_tool.py`, MEMORY.md | hermes-agent.nousresearch.com/docs/user-guide/features/memory | ~800 |
| P3 (Pre-Eviction Flush) | `run_agent.py` compression pipeline | hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching | ~600 |
| P4 (Procedural Memory) | `tools/skill_manage_tool.py`, skills/ | hermes-agent.nousresearch.com/docs/user-guide/features/skills | ~1,200 |
| P5 (Session Search) | `tools/session_search_tool.py`, `hermes_state.py` | hermes-agent.nousresearch.com/docs/developer-guide/session-storage | ~400 |
| P6 (Dual-Peer Modeling) | `honcho_integration/`, Honcho API | hermes-agent.nousresearch.com/docs/user-guide/features/honcho | ~1,000 |
| P7 (Delegation) | `tools/delegate_task_tool.py` | hermes-agent.nousresearch.com/docs/reference/tools-reference | ~300 |
| P8 (Zero-Context Pipelines) | `tools/execute_code_tool.py` | hermes-agent.nousresearch.com/docs/reference/tools-reference | ~200 |
| P9 (Prompt Stability) | `agent/prompt_builder.py`, `run_agent.py` | hermes-agent.nousresearch.com/docs/developer-guide/prompt-assembly | ~400 |
| P10 (Cron Clean Sessions) | `cron/`, `tools/cronjob_tool.py` | hermes-agent.nousresearch.com/docs/reference/tools-reference | ~200 |

## Appendix B: Full Config Flag Registry (AR-*)

```yaml
harness:
  agentic:
    # Existing flags (AI-1–5) ...
    tiered_eviction:
      enabled: true
    context:
      enabled: true
    checkpoints:
      enabled: true
    fact_extraction:
      enabled: true

    # New flags (AR-1–7)
    memory_capacity:
      enabled: true
      max_chars: 3000
      max_facts: 30
    self_model:
      enabled: true
      max_chars: 1500
    pre_eviction_flush:
      enabled: true
    session_search:
      enabled: true
      search_dirs: [briefings, summaries]
      max_results: 5
    procedural_memory:
      enabled: true
      min_steps_to_trigger: 5
      max_procedure_chars: 1500
    delegation:
      enabled: true
      default_budget: 10
      max_budget: 20
      fallback_mode: prompt  # "prompt" or "subagent"
```
