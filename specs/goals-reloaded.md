# Goals Reloaded — GTD-Informed Goal Intelligence Engine

**Spec ID:** GOALS-RELOADED v5.0  
**Author:** Artha Enhancement Pipeline  
**Date:** 2026-03-26  
**Revised:** 2026-03-26 (v5.0: boundary-safe goals substrate)  
**Status:** Proposed  
**Depends on:** None  
**Blocked by:** None  
**Estimated LOC:** ~40 (new `goals_writer.py`) + ~230 (modifications to existing files)

**Core thesis:** Build a native goals substrate that fixes schema drift,
restores deterministic goal signals, and makes goals influence
prioritization and review across Artha — while preserving prompt-first
reasoning, Work OS isolation, and silence-by-default UX.

---

## 0. Design Principles

This spec adheres to Artha's core architecture:

- **"Claude Code IS the application"** (Tech Spec §1.1) — no new scripts for
  reasoning work Claude already does. Scripts only where deterministic writes
  are needed.
- **"Zero custom code is the target"** (Tech Spec §1.1.4) — but "add helper
  scripts only when Claude proves unreliable at a specific step." Dual-layer
  YAML sync is the canonical case.
- **"State lives in Markdown/YAML"** — one file per concern, no shadow caches.
- **P3 Goals above tasks** (PRD) — features without a goal connection are
  deprioritized. This spec makes that principle operational.
- **P6 Earned Autonomy** (PRD) — the system proposes, the human decides.
- **UX-1 Silence is default** — goal intelligence is invisible unless something
  needs attention. One weekly cadence, not two.
- **UX-5 Conversation, not configuration** — goals are created through natural
  conversation, never forms or YAML editing.
- **Goals are gravitational, not decorative** — goals don't just appear in
  GOAL PULSE; they shape what the LLM prioritizes at Step 8, which item
  becomes the ONE THING at Step 11, and what `/power` surfaces. A goal that
  doesn't influence daily decision-making is decoration, not direction.

### Design Axiom

> If a feature can be implemented by adding a directive to the workflow prompt
> (LLM layer), don't implement it as a Python script — **unless the task is
> mechanical data synchronization**, where LLMs are demonstrably unreliable.
> New Python for reasoning = technical debt. New prompt directives = compound
> interest. New Python for deterministic writes = necessary infrastructure.

---

## 1. Problem Statement

Artha's goal system is structurally broken. Goals exist in `state/goals.md` as
a static Markdown table with no machine-readable per-goal metadata. The system
cannot detect stale goals, cannot link actions to goals, and cannot nudge users
toward progress. Specifically:

1. **PAT-003 is dead code.** The "Goal milestone stale" pattern expects
   `source_path: goals` to resolve to a list of dicts with `last_updated` per
   goal. The actual `goals.md` frontmatter contains only file-level keys
   (`schema_version`, `domain`, `last_updated`, `sensitivity`, `encrypted`).
   `_resolve_documents()` returns `[]` → PAT-003 never fires.  
   _Verified: `python3 -c "next(yaml.safe_load_all(open('state/goals.md').read())).get('goals')"` → `None`_

2. **Zero OI-to-goal linkage.** `state/open_items.md` has no `goal_id` field.
   `todo_sync.py` marks items done but never touches `goals.md`. Of 26 OIs
   (16 open, 10 done), zero reference a goal.

3. **Coaching engine is not integrated.** `scripts/coaching_engine.py` exists
   (E16, ACT-RELOADED) with 350 LOC of working strategy selection, suppression
   tracking, and anti-nagging logic — but no script or workflow step calls it.
   `grep -rn 'CoachingEngine' scripts/*.py` (excluding itself) returns zero
   results. The deterministic engine was deliberately moved from Step 19 to
   Step 8 in ACT-RELOADED, but the call site was never wired up.

4. **Goals have no GTD "Clarify" or "Reflect" steps.** There is no `next_action`
   field on any goal. There is no weekly review. Azure AI Certification has been
   🔴 Not Started for 85+ days with zero system awareness.

5. **No "parked" status.** Goals are either In Progress, Not Started, Upcoming,
   or Done. There's no way to intentionally defer a goal without deleting it,
   which means abandoned goals rot silently.

6. **No goal creation path.** F13.1 (P0) defines conversational goal creation,
   but no prompt directive guides the LLM through creating a structured goal
   entry when the user says "I want to start saving for college." Goals only
   exist because they were manually authored in Markdown.

7. **Habits are orphaned.** `state/goals.md` has a "Habits & Streaks" section
   with 6 habits (hiking, weight tracking, Bhagavatam study, cycling,
   Vipassana, Vedic study) that exist as a disconnected Markdown island — no
   staleness detection, no coaching integration, no `/goals` visibility.

### What gets worse without this fix

- PAT-003 remains dead, goal staleness undetectable
- Coaching engine remains unused (~350 LOC of dead code)
- Azure AI cert continues to show 🔴 without any nudge or park decision
- Weight loss goal at 57% has no linked OIs driving it
- No connection between "what I do" (OIs) and "what I want" (goals)
- New goals can only be created by manual Markdown editing (violates UX-5)

---

## 2. Assumptions (Tested)

| # | Assumption | Test | Result | Impact |
|---|-----------|------|--------|--------|
| A1 | Domain-based auto-linking can connect most OIs to goals | Mapped 8 goal domains vs 7 OI domains | **FAILED: Only 12% (3/26) auto-linkable** — kids(11), finance(6), insurance(3) have no goals | Domain auto-linking is NOT viable as primary mechanism |
| A2 | PAT-003 is broken due to missing `goals` list in frontmatter | `yaml.safe_load_all()` on goals.md, checked `data.get('goals')` | **Confirmed: returns `None`** | Must add `goals` YAML block to goals.md |
| A3 | Coaching engine is never called | `grep -rn 'CoachingEngine' scripts/*.py` excluding self | **Confirmed: zero callers** | Wire structured `goals` data into `coaching_engine.py` at Step 8 (ACT-RELOADED design intent) |
| A4 | Most OIs are operational, not goal-driven | Analyzed domain distribution: kids=11, finance=6, insurance=3 | **Confirmed: ~88% of OIs have no corresponding goal** | System must accept goal-independent OIs as normal |
| A5 | Goals.md Markdown tables are the LLM's primary data | Coaching engine already has `_extract_goals()` regex fallback; LLM reads tables natively | **Confirmed: tables are the authoritative source for the LLM** | YAML `goals` block is a thin index for deterministic Python only |
| A6 | Sprint format exists in registry | `config/registry.md` defines `sprints:` with `linked_goal` field | **Confirmed: spec exists, not implemented in goals.md** | Unify per-goal as optional `sprint:` sub-block |
| A7 | Catch-up frequency is ~1.3/day | `catch_up_count: 25` over 19 days | **Confirmed** | Weekly review fires within existing weekly summary (Monday or first catch-up after Sunday) |
| A8 | OI completion rate is meaningful | 10 done / 26 total = 38% | **Confirmed: healthy signal** | LLM can infer OI→goal links at Step 8 without explicit `goal_id` field |
| A9 | LLM improves over time | Artha's prompt-first architecture means better models = better Artha | **Axiom** | Prefer prompt directives over Python scripts for any reasoning task |
| A10 | Coaching engine was deliberately moved from Step 19 to Step 8 in ACT-RELOADED | Read PRD E16, Tech Spec, UX §8.6 | **Confirmed: deterministic script at Step 8, not LLM prompt at Step 19** | Must wire `goals` data into existing engine, not replicate its logic in a prompt directive |
| A11 | `goals_view.py` has no regex fallback — only `_parse_goals_index()` | Read full source: regex searches for `goals_index:\n`, returns empty if not found, prints "No goals found" | **Confirmed: zero fallback** | Must update `goals_view.py` in same phase as schema change |
| A12 | Step 3 periodic triggers live in `config/Artha.core.md`, not `config/workflow/process.md` | `process.md` covers Steps 5–7b; Step 3 is at lines 222–250 of `Artha.core.md` | **Confirmed** | All Step 3 modifications target `Artha.core.md` (and mirrored `Artha.md`) |
| A13 | Weekly summary already triggers on Monday (or first catch-up after Sunday 8PM) | `Artha.core.md` Step 3: `generate_weekly_summary = true` on Mondays | **Confirmed** | Goal weekly review folds INTO existing weekly summary — no separate Sunday trigger |
| A14 | LLMs are unreliable at mechanical dual-layer data synchronization | Tech Spec §1.1.4: "Add helper scripts only when Claude proves unreliable" | **Axiom** | Deterministic `goals_writer.py` handles YAML mutations; LLM handles Markdown tables |

### Key Correction from Prior Proposal

The earlier "87% auto-link rate by domain" estimate was **wrong**. Tested
against real data: only 3 of 26 OIs (12%) map to goal domains. The
majority of OIs are operational tasks (kids' school forms, insurance quotes,
finance admin) that exist independently of any strategic goal. The design
must accept this reality rather than force artificial linkage.

---

## 3. Design

### 3.1 Core Principle: Goals ≠ Tasks

GTD distinguishes "projects" (multi-step outcomes) from "next actions" (single
physical steps). Artha goals are GTD projects. OIs are either:
- **Goal-linked actions** — concrete next steps advancing a goal (~12% today)
- **Operational tasks** — reactive items with no strategic goal (insurance, kids, finance)

The system MUST NOT:
- Force every OI into a goal (creates noise, graveyard effect)
- Auto-link by domain alone (12% accuracy is unacceptable)
- Require manual linking for every OI (too much friction)

The system SHOULD:
- Require every active goal to have a `next_action` (GTD Clarify)
- Surface "goalless" periods where no OIs drive any goal
- Support deliberate parking of goals (GTD Someday/Maybe)
- Run a lightweight weekly review (GTD Reflect)

### 3.2 Dual-Layer Data Contract

Artha's LLM IS the engine. Goals have two layers, each serving a distinct
consumer:

| Layer | Consumer | Format | Source of truth for |
|-------|----------|--------|---------------------|
| **Markdown tables** (body) | LLM | Free-form prose + tables | Reasoning, coaching narrative, weekly review generation, goal creation |
| **`goals` YAML block** (frontmatter) | Deterministic Python | Structured list-of-dicts | PAT-003 staleness, `goals_view.py` CLI, `coaching_engine.py` strategy selection |

**Markdown tables are the LLM's primary data.** The LLM reads them natively,
reasons about them, generates coaching narratives from them, and writes updates
to them during Step 7 (domain file update). As models improve, the reasoning
quality improves automatically — no code changes required.

**The `goals` YAML block is a thin index for deterministic Python only.** It
exists so that:
1. PAT-003 can fire when a goal is stale (pattern engine needs structured data)
2. `goals_view.py` can render a CLI table without regex parsing
3. `coaching_engine.py` can select nudge strategies with real structured data
   (replacing the fragile emoji-heuristic fallback)

**Sync rule: deterministic writer, not LLM sync.**

The v2.0 spec proposed "The LLM maintains both layers at Step 7." This is
rejected because dual-layer YAML synchronization is exactly the scenario
Tech Spec §1.1.4 describes: "Add helper scripts only when Claude proves
unreliable at a specific step." LLMs are demonstrably unreliable at
mechanical data synchronization — 8 goals × 6+ mutable fields × 1.3
catch-ups/day = ~60 field-syncs per day with risk of format drift,
indentation errors, and date quoting inconsistencies.

**Instead: `goals_writer.py` (~40 LOC) as deterministic YAML writer.**

The LLM:
- Reasons from and writes to Markdown tables (its strength)
- Calls `goals_writer.py` for all YAML mutations:

```bash
# Update a goal field
python3 scripts/goals_writer.py --update G-002 \
  --next-action "Weigh in Saturday" --metric-current 177

# Park a goal
python3 scripts/goals_writer.py --update G-003 \
  --status parked --parked-reason "Deferring until XPF ramp stabilizes"

# Create a new goal
python3 scripts/goals_writer.py --create \
  --id G-009 --title "Save for Arjun college" --type outcome \
  --category finance --target-date 2030-06-01
```

This follows the proven `health_check_writer.py` pattern: atomic POSIX
write with lock, CLI flags for each field, deterministic output. YAML is
always correct. Markdown tables are always correct. No sync drift.

**Why this is the right exception to "zero new scripts":** It's a ~40 LOC
deterministic writer for exactly the failure mode the Tech Spec anticipated.
The design axiom says "unless the task is mechanical data synchronization" —
this is the canonical case.

#### Schema: `goals` YAML Block

Add a structured `goals` block **inside** `goals.md` frontmatter, keeping
the existing Markdown tables as the LLM's primary data layer.

```yaml
---
schema_version: "2.0"
domain: goals
last_updated: "2026-03-26"
sensitivity: medium
encrypted: false
goals:
  - id: G-001
    title: "Summit Mailbox Peak"
    type: milestone          # outcome | habit | milestone (PRD §7)
    category: fitness
    status: active           # active | parked | done | dropped
    next_action: "Register for Nadaan Parinde Saturday hike"
    next_action_date: "2026-03-29"
    review_date: "2026-03-30"
    last_progress: "2026-03-15"
    created: "2026-01-01"
    target_date: "2026-08-15"
    leading_indicators: []   # stub — auto-populated by Step 8m after 30 catch-ups
    # linked_ois: []         # Phase 2 — deferred until ephemeral linkage proves valuable
  - id: G-002
    title: "Lose 40 lbs (from 200 lb baseline)"
    type: outcome            # quantitative goal with metric
    category: health
    status: active
    next_action: "Weigh in this Saturday morning"
    next_action_date: "2026-03-29"
    review_date: "2026-03-30"
    last_progress: "2026-03-15"
    created: "2026-01-01"
    target_date: "2026-12-31"
    leading_indicators: []
    metric:                  # optional — for quantitative goals
      current: 177
      target: 160
      unit: lb
      direction: down        # down | up
  - id: G-003
    title: "Azure AI Certification"
    type: milestone
    category: learning
    status: parked
    next_action: null
    next_action_date: null
    review_date: "2026-04-06"
    last_progress: null
    created: "2026-01-01"
    target_date: "2026-12-31"
    leading_indicators: []
    parked_reason: "Deferring until XPF ramp stabilizes"  # present only when status: parked
    parked_since: "2026-03-26"                            # present only when status: parked
---
```

**Key schema decisions:**

- **`type: outcome | habit | milestone`** — matches PRD §7 Goal Model. Enables
  F13.10 (trajectory forecasting → Outcome only), F13.11 (behavioral nudge →
  Habit only), UX §8.6 Celebration (milestone-level only). Costs zero runtime.

- **`parked_reason` and `parked_since` are conditional** — only present when
  `status: parked`. The LLM/`goals_writer.py` adds them when parking, removes
  when reactivating. Cleaner schema, less noise on active goals.

- **`leading_indicators: []`** — stub field, auto-populated by Step 8m
  auto-discovery after 30 catch-ups (PRD §8.11). Costs nothing to include now,
  prevents schema migration later.

- **No `ignore_count`, no `goal_id` on OIs, no per-goal interaction tracking.**
  The LLM handles engagement reasoning; Python just needs IDs, statuses, dates,
  optional metrics, and optional leading indicators.

**`metric` sub-block:** Optional. Enables `goals_view.py` to render progress
bars (`177 lb → 160 lb ▓▓▓▓▓▓░░░░ 57%`) and the coaching engine to detect
pace deviations. Only relevant for Outcome goals with quantitative targets.
Omit entirely for Milestone goals (fitness peaks, certifications).

**`sprint` sub-block:** Optional. Adds per-goal sprint tracking. The
existing top-level `sprints:` block in `config/registry.md` is **preserved
in v1 for backward compatibility** — Step 3 sprint calibration in
`Artha.core.md` reads `state/goals.md → sprints` and must not silently
break. In v1, both representations coexist: the top-level `sprints:` block
remains the source for Step 3 calibration; the per-goal `sprint:` sub-block
is a convenience for display. **Phase 2:** Migrate Step 3 calibration to
read per-goal `sprint:` sub-blocks and deprecate top-level `sprints:`.

### 3.2.1 Work Goals and the Work OS Boundary

Artha's Work OS enforces strict isolation: `/work` reads only from
`state/work/` and never accesses personal state (`config/commands.md`,
`config/skills.yaml`, `config/agents/artha-work.md`). Putting work goals
(G-004 through G-008) in `state/goals.md` would violate this boundary.

**Design:** Personal goals (G-001 through G-003) live in `state/goals.md`.
Work goals live in `state/work/work-goals.md` with the identical `goals`
YAML schema. Each file is owned by its respective surface:

| File | Surface | Reader | Writer |
|------|---------|--------|--------|
| `state/goals.md` | Personal catch-up, `/goals`, `/power` | LLM + `goals_view.py` + `scorecard_view.py` + `coaching_engine.py` + `pattern_engine.py` | LLM (Markdown) + `goals_writer.py` (YAML) |
| `state/work/work-goals.md` | `/work`, `/work sprint` | Work OS LLM + `goals_view.py --scope work` | Work OS LLM (Markdown) + `goals_writer.py --file state/work/work-goals.md` (YAML) |

**Unified view:** `/goals` reads both files and merges them for display.
The personal catch-up surface may reference work goals read-only for the
GOAL PULSE section (same as bridge artifacts — read-only, no writes).
`/work` never reads `state/goals.md`.

**PAT-003 runs against both files independently** — one pattern instance
per `source_file`. Work goals get staleness alerts but NOT auto-park
(PAT-003b targets `state/goals.md` only).

`goals_writer.py` accepts `--file <path>` to target either file (defaults
to `state/goals.md`).

### 3.3 OI-to-Goal Linking: LLM Inference (No `goal_id` Field)

The prior design proposed adding a `goal_id` field to every OI in
`open_items.md`. This is rejected because:

1. **88% of OIs have no goal** — adding `goal_id: ""` to 26 items creates
   noise for zero value
2. **Linking is a reasoning task** — the LLM can infer which OIs advance
   which goals by reading both files at Step 8 (cross-domain reasoning)
3. **Explicit linkage creates friction** — users must manually tag or
   confirm, adding ceremony to every OI interaction

**Instead:** The LLM performs OI→goal linkage at Step 8 cross-domain reasoning.
When the LLM finds an active OI that clearly advances a goal, it mentions the
connection in the briefing narrative ("OI-025 'weigh in Saturday' advances
G-002 Weight Loss"). No field mutation, no schema change, no sync burden.

**OI linkage is ephemeral reasoning in Phase 1, not persisted state.**
The LLM mentions OI→goal connections in the briefing narrative and weekly
review, but does NOT persist `linked_ois` in Phase 1. Rationale: only ~12%
of OIs are goal-advancing, so persisting inferred links on every catch-up
would create churn, stale references, and extra `goals_writer.py` calls
without proven user value.

**Phase 2 (deferred):** If weekly reviews show that OI→goal linkage is
valuable for trend analysis, add `linked_ois: []` to the schema and
persist via `goals_writer.py`. Until then, inference is sufficient.

**Add to `prompts/goals.md`:** A directive instructing the LLM to scan
`open_items.md` for OIs that advance active goals and mention connections in
the briefing.

### 3.4 Weekly Goal Review: Folded Into Existing Weekly Summary

The prior design proposed a separate `goal_weekly_review = true` Sunday trigger.
This is rejected because it creates **two goal reviews in 24 hours**:

- **Sunday**: `goal_weekly_review` → Weekly Goal Review section
- **Monday**: `generate_weekly_summary` → Weekly Summary with "Goal Progress"
  section (Tech Spec §5.2)

This violates UX-1 (silence is default) and creates notification fatigue.

**Instead:** The Weekly Goal Review is a **subsection of the existing weekly
summary**, not a separate trigger. `Artha.core.md` Step 3 already sets
`generate_weekly_summary = true` on Mondays (or first catch-up after Sunday
8PM per Tech Spec §5.2). The goal review rides along.

**One weekly cadence. One flag. One review.**

**LLM directive (added to `config/Artha.core.md` Step 10, where weekly summary
is generated):**
When `generate_weekly_summary == true`, include an expanded **§ Goal Review**
section within the weekly summary. For each active goal in `goals`:

| Condition | Label | LLM Action |
|-----------|-------|------------|
| `last_progress` > 14d AND no `next_action` | **STALE** | Ask: "Park or act? What's the next concrete step?" |
| `next_action_date` is past | **NEEDS_ACTION** | Ask: "Next action expired. What replaces it?" |
| `last_progress` ≤ 14d AND valid `next_action` | **ON TRACK** | Brief acknowledgment only |
| `metric.current` deviating from pace | **OFF PACE** | Note deviation, suggest adjustment |
| `type: habit` AND streak broken ≥7d | **STREAK BROKEN** | Note gap, suggest restart micro-step |

For parked goals where `parked_since` > 30d: "G-003 still parked (30d).
Reactivate, keep parked, or drop?"

In flash mode: show only STALE, NEEDS_ACTION, and OFF PACE (skip ON TRACK).

**After review:** During the catch-up interaction, the LLM updates Markdown
tables per user decisions (park, set next_action, drop) and calls
`goals_writer.py` for YAML mutations. This happens at Step 7 domain file
update — no separate script needed.

**Sync verification (after every `goals_writer.py` call):** After executing
a `goals_writer.py` command, the LLM reads back the YAML `goals` block and
confirms it agrees with the Markdown table on: status, next_action, and
metric values. If drift is detected (“YAML says G-002 status: active but
Markdown table says Done”), the LLM calls `goals_writer.py --update` to
reconcile. This check adds <5 seconds and prevents the primary failure mode
of dual-layer architectures: silent divergence.

**Add to `config/briefing-formats.md`:** A §8.X template for the Goal Review
subsection within the weekly summary format (§8.6).

### 3.5 PAT-003 Fix + Auto-Park Pattern

#### PAT-003: Goal Milestone Stale

Update `config/patterns.yaml` PAT-003 to match new schema:

```yaml
- id: "PAT-003"
  name: "Goal milestone stale"
  description: "Fires when an active goal has had no progress update for 14+ days"
  source_file: "state/goals.md"
  source_path: "goals"                # <- was "goals" (unchanged key name)
  condition:
    all_of:
      - field: "status"
        eq: "active"                   # <- only check active goals
      - field: "last_progress"
        stale_days: 14
  output_signal:
    signal_type: "goal_stale"
    domain: "goals"
    urgency: 1
    impact: 2
    entity_field: "title"
    metadata: {}
  cooldown_hours: 72
  enabled: true
```

This works because `_resolve_documents()` finds `goals` as a list of dicts,
and `_evaluate_condition_block()` checks both `status == active` and
`last_progress` staleness. No changes to `pattern_engine.py` needed.

#### PAT-003b: Auto-Park Candidate Detection

New pattern that fires when a goal is likely abandoned:

```yaml
- id: "PAT-003b"
  name: "Goal auto-park candidate"
  description: "Fires when an active goal has no progress, no next_action, and is >30d old"
  source_file: "state/goals.md"
  source_path: "goals"
  condition:
    all_of:
      - field: "status"
        eq: "active"
      - field: "last_progress"
        is_null: true
      - field: "next_action"
        is_null: true
      - field: "created"
        stale_days: 30
  output_signal:
    signal_type: "goal_autopark_candidate"
    domain: "goals"
    urgency: 2
    impact: 2
    entity_field: "title"
    metadata: {}
  cooldown_hours: 168
  enabled: true
```

**Auto-park rule (data-driven, no interaction tracking):** A goal is an
auto-park candidate when: `status == active` AND `last_progress == null` AND
`next_action == null` AND `created` > 30d ago. When PAT-003b fires, the LLM
includes an auto-park recommendation in the briefing. The user confirms or
rejects during the catch-up interaction. No `ignore_count` field needed.

**Note:** `_evaluate_condition_block()` needs a small addition to support
the `is_null` operator. This is ~5 lines in `pattern_engine.py`. **Must be
defensive for LLM-written data:**

```python
def _is_null(value) -> bool:
    """True if value is None, empty string, or key was missing."""
    return value is None or value == "" or value == "null"
```

LLM-written YAML has semantic ambiguity: `next_action: null` → `None`,
`next_action:` → `None` or `""`, `next_action: ""` → `""`, missing key →
`KeyError`. The operator must treat all these as null.

### 3.6 Coaching Engine Integration (Step 8, Not Step 19b)

`scripts/coaching_engine.py` (~350 LOC) exists but is never called. The v2.0
spec proposed "Primary: LLM-driven coaching at Step 19b." This is rejected
because it contradicts the ACT-RELOADED architecture:

- **E16 deliberately moved coaching from Step 19 (LLM prompt) to Step 8
  (deterministic script).** The Python engine has working strategy selection
  (4 types: obstacle anticipation, progress reflection, next small win,
  cross-domain insight), suppression tracking (7-day per-type, 14-day global
  after 2 consecutive dismissals), rotation across goals, and style
  preferences (question/direct/cheerleader).

- **UX §8.6** defines specific rules: max 1 nudge per catch-up, dismissal
  suppresses for 7 days, R4 auto-disables coaching if always dismissed.

- **An LLM prompt directive CAN'T reliably enforce** "max 1 nudge per
  catch-up, rotate across goals, respect 7-day suppression, track dismissal
  counts." These require state tracking, which is what the script does.

**Corrected integration — two layers, right roles:**

| Layer | Role | What it does |
|-------|------|-------------|
| **`coaching_engine.py` at Step 8** | Strategy selection (deterministic) | Reads `goals` YAML block → selects nudge type + target goal. Enforces suppression, rotation, dismissal tracking. Outputs structured nudge spec. |
| **LLM at Step 19b** | Narrative generation (reasoning) | Takes the structured nudge spec → generates human-readable coaching text with context, tone, and empathy. Presents the nudge selected by the engine. |

**The engine decides *what* to say. The LLM decides *how* to say it.**

**`coaching_engine._extract_goals()` already looks for `goals.get("goals", [])`
in frontmatter.** By naming the YAML key `goals` (not `goals_index`), the
extraction works with **zero code changes** to the engine itself. What changes:

1. A call site must be added — the workflow invokes `coaching_engine.py` at
   Step 8, passing the structured `goals` data. The engine returns a nudge
   spec (goal ID, nudge type, strategy).

   **Concrete call-site contract:** At Step 8, the LLM executes:
   ```bash
   python3 scripts/coaching_engine.py --goals-file state/goals.md --format json
   ```
   The engine reads the `goals` YAML block from the specified file, selects a
   nudge, and outputs a JSON nudge spec to stdout:
   ```json
   {"goal_id": "G-002", "nudge_type": "next_small_win", "strategy": "question", "suppressed": false}
   ```
   If all nudges are suppressed, it outputs `{"suppressed": true}`. The LLM
   captures this output and carries it to Step 19b. If the engine returns a
   non-zero exit code, the LLM skips coaching silently (UX-1).

2. Step 19b directive says: "Present the nudge that `coaching_engine.py`
   selected: [nudge_spec]. Generate a 2-line coaching message matching the
   user's preferred coaching style."

**On weekly summary days:** The coaching nudge is folded into the Goal Review
section instead of appearing separately — the LLM integrates it naturally.

**Pace detection comes alive.** The coaching engine defines
`_GOAL_PACE_DEVIATION_PCT = 20` but never uses it (no `metric` data existed).
With `type: outcome` goals now having `metric: {current, target}`, the engine
can compute pace deviation: `(expected_progress - actual_progress) / total >
_GOAL_PACE_DEVIATION_PCT%`. No code change needed — the data now exists.
**Deprecation trajectory.** The Python coaching engine exists because current
LLMs cannot reliably enforce suppression windows, rotation across goals, or
dismissal counters across a multi-minute reasoning session. As model
capabilities improve (reliable tool-use state, session-spanning counters),
the engine's deterministic constraints may become expressible as prompt
directives. **Monitor:** If after 30 catch-ups the LLM's Step 19b output is
consistently better quality than the engine's strategy selection, begin
migrating suppression logic to a simpler JSON counter file and let the LLM
handle full strategy. Until then, the two-layer pattern (engine decides what,
LLM decides how) is the correct architecture. Do not prematurely deprecate.
### 3.7 Prompt Directives

Instead of writing Python scripts for reasoning tasks, add directives to
existing prompt and workflow files:

**`prompts/goals.md` additions:**
1. **Goal creation directive:** "When the user defines a new goal through
   conversation (F13.1), propose the full structured entry for confirmation
   per UX §8.1: infer goal type (outcome/habit/milestone), suggest metrics,
   identify data sources. On confirmation, update the Markdown table AND
   call `goals_writer.py --create` with the structured fields."
2. **OI→goal inference directive:** "During cross-domain reasoning (Step 8),
   scan `open_items.md` for any open OIs that advance an active goal. Mention
   connections in the briefing narrative. Do not modify `open_items.md`."
3. **Table→YAML coordination directive:** "When updating goals in the Markdown
   tables, call `goals_writer.py --update G-NNN --field value` for each
   changed YAML field. The LLM owns Markdown tables; the script owns YAML."

**`config/Artha.core.md` Step 10 (weekly summary generation):**
- Add: "When `generate_weekly_summary == true`, include a **§ Goal Review**
  subsection within the weekly summary per the template in
  `config/briefing-formats.md` §8.X. Use STALE / NEEDS_ACTION / ON TRACK /
  OFF PACE labels per goal. For Habit goals, check streak status."

**`config/Artha.core.md` Step 8 (cross-domain reasoning):**
- Add: "Invoke `coaching_engine.py` with structured `goals` data. The engine
  returns a nudge spec (goal ID, nudge type, strategy). Include the nudge
  spec in the harness for Step 19b presentation."
- Add: "During ORIENT (Step 8-Or), for the `Goals × [All Domains]` pair,
  execute the **Goal Evaluation Protocol** — not the vague 'Which goals made
  progress?' but explicitly:
  1. For each active goal in `goals` YAML: check if any OIs completed since
     last catch-up advance this goal. If yes, note the connection and update
     `linked_ois` via `goals_writer.py`.
  2. Flag any goal where `next_action_date` is in the past.
  3. Flag any goal where `last_progress` > 14 days (pre-empting PAT-003).
  4. For `type: outcome` goals with `metric`: compute whether pace is on/off
     track against `target_date`.
5. Carry all goal findings forward to Step 8-D for U×I×A scoring.
   6. Update `linked_ois` via `goals_writer.py` only in Phase 2 (see §3.3).
      In Phase 1, mention connections in narrative only."

**`config/workflow/reason.md` Step 8-D (ONE THING selection):**
- Add: "When scoring candidate items for ONE THING, apply a **goal-alignment
  tie-breaker**: if two candidates score equally on U×I×A *and* one advances
  an active goal (identified in the Goal Evaluation Protocol above), prefer
  the goal-advancing item. Goal alignment breaks ties — it does not override
  genuine urgency. Most real-life OIs are operational (kids, insurance,
  finance); goal alignment should elevate strategic work when urgency
  permits, not routinely overpower real-world obligations. This
  operationalizes P3 ('Goals above tasks') without distorting triage."

**`config/commands.md` `/power` amendment:**
- After step 1 ("Lists all open items due ≤7 days"), add step 1.5: "**Goal
  Check:** Surface any active goals where `next_action_date` is today or
  past, OR where `last_progress` > 14 days. Max 2 lines. Example:
  '⚡ G-002 next action overdue (weigh in was due Saturday). G-003 still
  parked 30d.' If all goals are healthy, skip silently (UX-1)."

**`config/briefing-formats.md` §8.1 (daily goal micro-check):**
- Below GOAL PULSE, add a conditional **Goal Heartbeat** (≤2 lines, non-weekly
  catch-ups only). Surfaces ONLY if any active goal has: `next_action_date`
  in the past, `last_progress` > 14 days, or metric pace deviation > 20%.
  Silent when all goals are on track (UX-1). This prevents the 5-day blind
  spot between weekly reviews — goals get a daily pulse check, not a daily
  review. Example: "⚡ G-002: next action overdue (weigh in due Sat) ·
  G-001: on track"

**`config/workflow/finalize.md` Step 19b:**
- Replace current vague prose with: "Present the coaching nudge selected by
  `coaching_engine.py` at Step 8. Generate a 2-line coaching message matching
  the user's preferred style from `memory.md → coaching_preferences`. On
  weekly summary days, fold the nudge into the Goal Review section."

**`config/briefing-formats.md`:**
- Add §8.X Goal Review template (subsection of §8.6 weekly summary) with
  STALE / NEEDS_ACTION / ON TRACK / OFF PACE / STREAK BROKEN label
  definitions and flash vs standard rendering rules.
- Add Goal Heartbeat template to §8.1 Standard Briefing (conditional ≤2
  lines below GOAL PULSE, non-weekly only, silent when all goals on track).

### 3.8 Goal Creation Path (F13.1)

F13.1 (P0) and UX §8.1 + UX-5 define conversational goal creation. Without
a creation directive, the only way to add a goal is manual Markdown editing —
violating UX-5 ("conversation, not configuration").

**Mechanism:** When the user says "I want to start saving for Arjun's
college" or "create a goal for...", the LLM:
1. Infers goal type (outcome), category (finance), suggests metrics
   (data source: Fidelity balance emails), proposes a target
2. Presents the full structured entry for confirmation (per UX §8.1 —
   "always proposes full structured goal, never creates silently")
3. On confirmation:
   - Adds a row to the Markdown table in `state/goals.md`
   - Calls `goals_writer.py --create --id G-NNN --title "..." --type outcome
     --category finance --target-date 2030-06-01 --metric-target 200000`
4. Both layers are populated in one interaction — no sync drift.

**Auto-wired metrics (F13.2):** The creation directive includes a lookup
table of domain → data source mappings (net worth → Fidelity emails; weight
→ manual weigh-in; exercise → Strava/calendar). Auto-wired metrics are
highlighted in the proposal: "📊 Metric auto-wired: Fidelity balance emails."

### 3.9 Habits: Deferred to Phase 2

`state/goals.md` has a "Habits & Streaks" section with 6 habits. The PRD
defines Habit as a first-class goal type (F13.11). This spec adds `type:
habit` to the schema but **defers habit-specific features to Phase 2:**

- Phase 1 adds `type: habit` goals to the YAML block (passive tracking)
- Phase 2 (separate spec) adds `cadence`, `streak_count`, `last_completed`
  fields and streak-specific coaching via `coaching_engine.py`

**Rationale:** Habit tracking has distinct mechanics (cadence, streaks,
implementation intentions) that warrant their own design. Including it here
would bloat the spec beyond its core mission: fix PAT-003, wire coaching,
enable weekly review.

### 3.10 FR-13 Coverage Matrix

FR-13 defines 18 features. This spec addresses 7 directly, enables 3 more,
and defers 8 to future specs:

| Feature | Status | Notes |
|---------|--------|-------|
| F13.1 Conversational Goal Creation | ✅ **This spec** | Creation directive in §3.8 |
| F13.2 Automatic Metric Collection | ⚠️ **Enabled** | `metric` sub-block + auto-wiring table in creation directive |
| F13.3 Goal Progress in Briefing | ✅ **Existing** | Goal Pulse already in briefing template |
| F13.4 Weekly Goal Review | ✅ **This spec** | Folded into weekly summary (§3.4) |
| F13.5 Goal Cascade View | 🔜 Deferred | Sub-goal hierarchy; needs `parent_id` field |
| F13.6 Goal-Linked Alerts | ⚠️ **Enabled** | PAT-003 fix links alerts to specific goals |
| F13.7 Recommendation Engine | ⚠️ **Enabled** | Coaching engine at Step 8 generates contextual recs |
| F13.8 Annual Retrospective | 🔜 Deferred | Year-end feature |
| F13.9 Goal Conflict Detection | 🔜 Deferred | Needs metric correlation analysis |
| F13.10 Trajectory Forecasting | 🔜 Deferred | Needs `type: outcome` (now available) + pace model |
| F13.11 Behavioral Nudge Engine | 🔜 Deferred | Habit-specific (Phase 2, §3.9) |
| F13.12 Dynamic Replanning | 🔜 Deferred | Needs trajectory data as input |
| F13.13 Seasonal Patterns | 🔜 Deferred | Needs 1 year of data |
| F13.14 Implementation Planning | ✅ **This spec** | Coaching engine obstacle anticipation strategy |
| F13.15 Obstacle Anticipation | ✅ **This spec** | Coaching engine strategy type 1 |
| F13.16 Accountability Patterns | ✅ **This spec** | Coaching engine style adaptation + dismissal tracking |
| F13.17 Sprint with Real Targets | ✅ **This spec** | `sprint` sub-block per goal |
| F13.18 Goal Auto-Detection | 🔜 Deferred | Needs pattern-of-life data (F15.34) |

---

## 4. Implementation Plan

### Phase 1: Schema, Goals Writer, PAT-003, View, and Prompt Directives (Day 1)

The highest-ROI changes: make goals machine-readable, fix PAT-003, enable
`/goals` rendering, and add creation + coaching directives. Everything in
one phase so the feature works end-to-end immediately.

| Step | File | Change | Risk |
|------|------|--------|------|
| 1.1 | `state/goals.md` | Add `goals` YAML block with 8 goals (3 personal + 5 work). Include `type` field on each. Include `metric` sub-block on G-002 (weight). Include `sprint` sub-block on G-001 if active sprint. `leading_indicators: []` on all. Keep existing Markdown tables unchanged. Bump `schema_version` to `2.0`. | **Low** — additive only, existing tables untouched |
| 1.2 | `scripts/goals_writer.py` | New ~40 LOC deterministic YAML writer. CLI flags: `--create`, `--update G-NNN`, `--status`, `--next-action`, `--metric-current`, `--parked-reason`. Atomic POSIX write with lock (same pattern as `health_check_writer.py`). | **Medium** — new file, but small and following established pattern |
| 1.3 | `config/patterns.yaml` | Fix PAT-003: `source_path: goals`, add `status: eq: active` condition. Add PAT-003b auto-park candidate pattern. | **Low** — PAT-003 was already non-functional |
| 1.4 | `scripts/pattern_engine.py` | Add `is_null` operator to `_evaluate_condition_block()` (~5 lines). Defensive: `value is None or value == "" or value == "null"`. | **Low** — small additive change |
| 1.5 | `scripts/goals_view.py` | Rewrite `_parse_goals_index()` → `_parse_goals_yaml()`: use `yaml.safe_load_all()` to parse the `goals` list from frontmatter instead of regex. Add `--scope work` flag to read `state/work/work-goals.md`. Add `type`, `next_action`, `days_since_progress` columns. Add `metric` progress bar support. | **Medium** — replaces fragile regex parser with structured YAML parsing |
| 1.5b | `scripts/scorecard_view.py` | Update `_parse_goals_index()` → `_parse_goals_yaml()` to match `goals_view.py` change. Same `yaml.safe_load_all()` approach. This is a **required** companion change — `scorecard_view.py` also parses the old `goals_index` shape and will break if only `goals_view.py` is updated. | **Medium** — same pattern as 1.5 |
| 1.6 | `prompts/goals.md` | Add goal creation directive (§3.8), OI→goal inference directive, table→YAML coordination directive. | **Low** — additive prompt text |
| 1.7 | `config/Artha.core.md` | Enrich Step 10 with Goal Review subsection in weekly summary. Enrich Step 8 with `coaching_engine.py` invocation directive. | **Low** — replaces existing vague prose with specific directives |
| 1.8 | `config/Artha.md` | Mirror Step 10 and Step 8 changes from `Artha.core.md`. | **Low** — keep files in sync |
| 1.9 | `config/workflow/finalize.md` | Update Step 19b: "Present the nudge selected by `coaching_engine.py` at Step 8." | **Low** — replaces vague prose |
| 1.10 | `config/briefing-formats.md` | Add §8.X Goal Review template (subsection of §8.6 weekly summary). Add Goal Heartbeat template to §8.1 (conditional ≤2 lines, non-weekly). | **Low** — additive template |
| 1.11 | `config/registry.md` | Document `goals` schema with `type` field and `sprint` sub-block. **Keep** top-level `sprints:` definition for backward compatibility; add note that per-goal `sprint:` is the future target. | **Low** — docs only |
| 1.14 | `state/work/work-goals.md` | New file — Work OS goals with same `goals` YAML schema. Bootstrap from current work goals in `state/goals.md` Markdown tables. Markdown body + YAML frontmatter, same dual-layer contract. | **Low** — additive, respects Work OS boundary |
| 1.15 | `state/templates/goals.md` | Update template to include `goals:` YAML block stub so new installations match the v2.0 schema. | **Low** — template only |
| 1.12 | `config/commands.md` | Add Goal Check step 1.5 to `/power` command. | **Low** — additive directive |
| 1.13 | `config/workflow/reason.md` | Add Goal Evaluation Protocol to Step 8 ORIENT. Add goal-alignment bonus to Step 8-D ONE THING selection. | **Low** — enriches existing OODA protocol |

**Verification:**
```bash
# PAT-003 fires for stale active goals
python3 -c "
from scripts.pattern_engine import PatternEngine
signals = PatternEngine().evaluate()
print([s for s in signals if s.signal_type == 'goal_stale'])
"

# goals_view.py renders from YAML
python3 scripts/goals_view.py --format standard

# goals_writer.py creates and updates
python3 scripts/goals_writer.py --update G-002 --next-action "Weigh in Saturday"
python3 scripts/goals_writer.py --update G-003 --status parked \
  --parked-reason "Deferring until XPF ramp stabilizes"
```

Expected: Active goals with `last_progress` > 14d fire PAT-003. Parked goals
do NOT fire. `/goals` shows all goals with progress bars and next_action.
Goals writer atomically updates YAML without touching Markdown tables.

### Phase 2: Coaching Wiring + End-to-End Validation (Day 2)

| Step | File | Change | Risk |
|------|------|--------|------|
| 2.1 | `scripts/coaching_engine.py` | No code changes needed — `_extract_goals()` already reads `goals.get("goals", [])`. Verify it works with real structured data instead of emoji heuristic fallback. | **Low** — read-only verification |
| 2.2 | Catch-up run | Run a weekday catch-up → verify GOAL PULSE appears, coaching nudge is generated from structured data, no Weekly Goal Review. | **Low** — validation only |
| 2.3 | Weekly catch-up | Run a Monday catch-up (or force `generate_weekly_summary = true`) → verify Goal Review subsection appears within weekly summary. | **Low** — validation only |
| 2.4 | Goal creation | Create a new goal via conversation → verify both Markdown table and YAML block are populated via `goals_writer.py --create`. | **Medium** — tests creation path end-to-end |
| 2.5 | Stale goal | Let a goal go stale >14d → verify PAT-003 fires. | **Low** — validation |
| 2.6 | Auto-park | Leave a goal with no progress/action for >30d → verify PAT-003b fires. | **Low** — validation |
| 2.7 | `python3 scripts/goals_view.py --format standard` | All goals render correctly with type, metrics, next_action. | **Low** — validation |

---

## 5. Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | User ignores weekly review entirely (skips Mondays) | Medium | High — goals stay stale | PAT-003 fires independently on non-Monday catch-ups. PAT-003b escalates to auto-park recommendation after 30d of no progress + no next_action. |
| R2 | `goals_writer.py` atomic writes conflict with LLM Markdown edits | Low | Medium | Clear separation: LLM owns Markdown body, `goals_writer.py` owns YAML frontmatter. Both edit the same file but different sections. Atomic POSIX write with lock prevents concurrent corruption. |
| R3 | User feels nagged by weekly review | Medium | Medium — disengagement | Flash mode only shows STALE/NEEDS_ACTION (skip ON TRACK goals). Auto-park removes persistent offenders from the review. Add `goal_review_enabled: true` flag in `artha_config.yaml` for kill switch. |
| R4 | Work goals updated from `/work` surface conflict with personal `/goals` updates | Low | Low | Work goals live in `state/work/work-goals.md` (Work OS boundary). Personal goals live in `state/goals.md`. Each surface writes only to its own file. `/goals` merges both read-only. No concurrent write risk. |
| R5 | Existing tests break due to goals.md schema change | Medium | Low | All test fixtures use isolated state dirs. Production `goals.md` change is additive (YAML block added to frontmatter). `goals_view.py` fallback updated in same phase. |
| R6 | PAT-003b false positive parks a goal user cares about | Low | Medium | PAT-003b only fires as a recommendation — the LLM suggests parking, user confirms. Not automated. Conditions are strict (no progress AND no next_action AND >30d). Easily reversible: "reactivate G-003". |
| R7 | `is_null` operator in pattern engine has edge cases | Low | Low | Defensive implementation: `value is None or value == "" or value == "null"`. Covered by unit test. Falls through to PAT-003 if PAT-003b misbehaves. |
| R8 | Coaching engine produces poor nudges with real data | Medium | Low | The engine already has 4 strategy types and style adaptation. Structured `goals` data only makes it more accurate than the emoji-heuristic fallback. R4 (briefing adapter) auto-disables coaching if always dismissed. Max 1 nudge per catch-up limits exposure. |
| R9 | `leading_indicators: []` stub is never populated | Medium | None | Stub is inert — costs nothing. Step 8m auto-discovery activates after 30 catch-ups. If it never triggers, the stub is harmless YAML. |
| R10 | Goal creation via conversation produces inconsistent YAML | Low | Medium | `goals_writer.py --create` enforces schema. LLM proposes values, user confirms, script writes deterministically. No LLM-generated YAML. |

---

## 6. Success Criteria

| Metric | Baseline (today) | Target (4 weeks) | Measurement |
|--------|-------------------|-------------------|-------------|
| PAT-003 fire rate | 0 (broken) | Fires for any active goal stale >14d | `pattern_engine_state.yaml` last_fired for PAT-003 |
| Goals with `next_action` set | 0/8 | ≥5/8 active goals | Count from `goals` YAML block |
| Weekly review presence | N/A (doesn't exist) | Appears in 100% of weekly summaries | Briefing archive check (Monday/Sunday catch-ups) |
| LLM OI→goal mentions | 0 | ≥2 connections mentioned per week | Briefing narrative search for "advances G-" |
| Azure AI Cert resolved | 🔴 Not Started 85d → no system response | Parked (with reason) OR has next_action | Check `goals` YAML status |
| Auto-park candidate surfaced | N/A | ≥1 PAT-003b fire for abandoned goal | `pattern_engine_state.yaml` |
| Coaching nudge from structured data | Generic (emoji fallback) | Nudge references specific goal ID and type from YAML | Step 8 coaching_engine output |
| `goals_view.py` renders from YAML | "No goals found" (broken) | Renders all goals with type, progress bars, next_action | CLI output verification |
| Goal created via conversation | 0 (no creation path) | ≥1 goal created conversationally via `goals_writer.py --create` | YAML block has entry not present at bootstrap |
| `goals_writer.py` uptime | N/A (new) | Zero failures in 4 weeks | Error log scan |
| Goal management overhead | N/A (no goal system) | ≤30s added per daily catch-up, ≤3 min on weekly review | Observed catch-up duration |
| Daily goal blind spots | 5+ days between reviews | Goal Heartbeat fires within 24h of any goal issue | Briefing archive check |

### Kill Criteria (abandon if)

- Weekly review section never appears after 4 weekly summaries (LLM ignores directive)
- PAT-003 still doesn't fire after schema fix (deeper pattern engine bug)
- `goals_writer.py` causes data corruption (>1 incident in first week)
- Coaching engine produces worse nudges with structured data than with emoji fallback

---

## 7. Files Changed

### New Files

| File | LOC | Purpose |
|------|-----|---------|
| `scripts/goals_writer.py` | ~40 | Deterministic YAML writer for `goals` block in `goals.md`. CLI flags: `--create`, `--update G-NNN`, `--status`, `--next-action`, `--metric-current`, `--parked-reason`, etc. Atomic POSIX write with lock. Follows `health_check_writer.py` pattern. |

### What Was Cut (vs v1.0)

| v1.0 Proposed | LOC | Reason Cut |
|---------------|-----|------------|
| `scripts/goal_review.py` | ~200 | Weekly review is a reasoning task → LLM directive in weekly summary |
| `goal_id` field on all 26 OIs | ~26 | OI→goal linkage is LLM inference at Step 8, not schema |
| `ignore_count` field + interaction tracking | ~50 | Auto-park uses data-driven PAT-003b pattern instead |
| `todo_sync.py` cross-file write to goals.md | ~25 | Goal progress updated by LLM at Step 7, not by Python |
| Sunday `goal_weekly_review` trigger | ~3 | Folded into existing Monday `generate_weekly_summary` — one weekly cadence |
| LLM-only coaching at Step 19b | ~15 | Coaching engine at Step 8 is deterministic; LLM presents at Step 19b |

### Modified Files

| File | Change Size | Purpose |
|------|------------|---------|
| `state/goals.md` | +50 lines | Add `goals` YAML block (8 goals with `type`, optional `metric`/`sprint`/`leading_indicators` fields) |
| `config/patterns.yaml` | ~10 lines | Fix PAT-003 `source_path`, add `status` condition. Add PAT-003b auto-park candidate. |
| `scripts/pattern_engine.py` | ~5 lines | Add defensive `is_null` operator to `_evaluate_condition_block()` |
| `scripts/goals_view.py` | +35 lines | Rewrite `_parse_goals_index()` → `_parse_goals_yaml()` using `yaml.safe_load_all()`. Add `--scope work` flag. Show type, next_action, staleness, metric progress bars |
| `scripts/scorecard_view.py` | +15 lines | Update `_parse_goals_index()` → `_parse_goals_yaml()` to match `goals_view.py`. Same YAML parsing approach. |
| `prompts/goals.md` | +20 lines | Goal creation directive (§3.8), OI→goal inference directive, table→YAML coordination directive |
| `config/Artha.core.md` | +10 lines | Enrich Step 10 (Goal Review in weekly summary) and Step 8 (coaching engine invocation) |
| `config/Artha.md` | +10 lines | Mirror `Artha.core.md` changes |
| `config/commands.md` | +5 lines | Add Goal Check step 1.5 to `/power` command |
| `config/workflow/reason.md` | +15 lines | Step 8 Goal Evaluation Protocol + ONE THING goal-alignment bonus |
| `config/workflow/finalize.md` | +5 lines | Update Step 19b to present coaching engine output |
| `config/briefing-formats.md` | +25 lines | Add §8.X Goal Review template as subsection of §8.6 weekly summary. Add Goal Heartbeat to §8.1 |
| `config/registry.md` | +10 lines | Document `goals` schema with `type` field, unify sprint sub-block |

| `state/work/work-goals.md` | +30 lines | Work goals file with same `goals` YAML schema, respecting Work OS boundary |
| `state/templates/goals.md` | +10 lines | Updated template with `goals:` YAML block stub |

**Total: ~40 LOC new (`goals_writer.py`) + ~230 LOC modifications across 14 existing files.**

### Test Files

| File | Tests | Purpose |
|------|-------|---------|
| `tests/unit/test_pattern_003_fix.py` | ~10 | PAT-003 fires correctly with new schema. PAT-003b fires for auto-park candidates. `is_null` operator: None, "", "null", missing key. |
| `tests/unit/test_goals_view_v2.py` | ~15 | `goals` YAML reading, format rendering, type column, metric progress bars |
| `tests/unit/test_goals_writer.py` | ~12 | Create, update, park, status transitions. Atomic write. Schema validation. |
| `tests/unit/test_scorecard_goals_v2.py` | ~8 | `scorecard_view.py` reads `goals` YAML correctly, computes score. |
| `tests/unit/test_work_goals_boundary.py` | ~6 | `/work` surface reads only `state/work/work-goals.md`, never `state/goals.md`. `/goals` merges both files. |
| `tests/unit/test_sprint_compat.py` | ~5 | Top-level `sprints:` still readable by Step 3 calibration after schema change. |

---

## 8. Migration Plan

### Backward Compatibility

- Existing `goals.md` Markdown tables are preserved as-is (LLM's primary data)
- `goals_view.py` reads from `goals` YAML block (updated in Phase 1)
- `scorecard_view.py` updated in same phase (also parses old `goals_index`)
- `coaching_engine._extract_goals()` works with both old (regex) and new
  (structured) format — key name `goals` matches its existing lookup
- Top-level `sprints:` block preserved for Step 3 calibration compatibility
- Work goals moved to `state/work/work-goals.md` (Work OS boundary respected)
- `open_items.md` is untouched — no `goal_id` field, no schema change
- `todo_sync.py` is untouched — no cross-file write added
- No breaking changes to any existing command or workflow
- `goals_writer.py` only writes YAML frontmatter; never touches Markdown body
- `state/templates/goals.md` updated to match v2.0 schema

### Bootstrap Sequence

1. **Create `scripts/goals_writer.py`** — deterministic YAML writer with
   CLI flags, atomic write, schema validation.
2. **Add `goals` YAML block** to `state/goals.md`:
   - Read existing Markdown tables
   - Generate `goals` entries with IDs G-001 through G-008
   - Set `type` per goal (outcome, milestone, habit)
   - Set `next_action: null` for all (user fills in during first weekly review)
   - Set `last_progress` from file-level `last_updated` (2026-03-15)
   - Add `metric` sub-block for weight goal (G-002)
   - Add `leading_indicators: []` on all goals
   - Set Azure AI Cert (G-003) to `status: active` (preserve current meaning;
     let the first weekly review decide whether to park)
3. **Update `goals_view.py`** — change regex from `goals_index:` to `goals:`,
   add `type` and `next_action` columns, add metric progress bars.
4. **Fix PAT-003** in `config/patterns.yaml`, add PAT-003b
5. **Add `is_null` operator** to `pattern_engine.py` (defensive)
6. **Add prompt directives** to `prompts/goals.md` (creation, inference, coordination)
7. **Enrich `Artha.core.md`** — Step 10 (Goal Review in weekly summary),
   Step 8 (coaching engine invocation)
8. **Update `workflow/finalize.md`** — Step 19b (present coaching engine output)
9. **Add review template** to `config/briefing-formats.md`
10. **Update `config/registry.md`** — document `goals` schema

### Rollback

If the feature doesn't work:
- Remove `goals` YAML block from `goals.md` (tables still work, LLM unaffected)
- Delete `scripts/goals_writer.py` (no dependencies)
- Revert `goals_view.py` regex change
- Revert PAT-003 (was already non-functional, no regression)
- Remove prompt directives (LLM reverts to current behavior)
- Revert Step 10 and Step 8 enrichments in `Artha.core.md`
- Remove briefing template
- Revert `is_null` operator in `pattern_engine.py`

**Rollback is clean** because no existing schemas were mutated (no `goal_id`
on OIs, no cross-file writes). `goals_writer.py` is self-contained with no
dependents.

---

## 9. Resolved Questions

These were open in v1.0 and are now resolved:

| # | Question | Resolution | Rationale |
|---|----------|-----------|-----------|
| 1 | Should work goals participate in weekly review? | **Yes, with lighter touch.** Work goals get staleness alerts via PAT-003 but NOT auto-park (PAT-003b targets `state/goals.md` only). Work goals are better served by `/work sprint` cadence. | Work goals live in `state/work/work-goals.md`; same schema, separate file. |
| 2 | How to handle quantitative goal metrics? | **Optional `metric` sub-block** on Outcome goals. `goals_view.py` renders progress bars. Coaching engine detects pace deviations. | PRD §7 defines Outcome/Habit/Milestone types; metrics only apply to Outcome. |
| 3 | Weekly review: Python script or LLM-generated? | **LLM-generated**, folded into existing weekly summary (Monday trigger). No separate Sunday trigger. | Reasoning task → prompt directive. UX-1: one weekly cadence, not two. |
| 4 | Should there be a separate Sunday `goal_weekly_review` trigger? | **No.** Fold into `generate_weekly_summary` (Monday). | Two goal reviews in 24h violates UX-1. Tech Spec §5.2 already generates weekly summary with Goal Progress section. |
| 5 | Who writes the YAML `goals` block — LLM or script? | **Script.** `goals_writer.py` (~40 LOC) is the deterministic writer. LLM owns Markdown tables. | LLMs are unreliable at mechanical dual-layer sync (Tech Spec §1.1.4). `health_check_writer.py` proves this pattern works. |
| 6 | Should coaching be LLM-only at Step 19b? | **No.** Coaching engine at Step 8 (deterministic strategy selection) + LLM at Step 19b (narrative presentation). | ACT-RELOADED E16 deliberately moved coaching to Step 8. The engine enforces suppression, rotation, and dismissal tracking that LLM prompts can't guarantee. |
| 7 | Should `goals_view.py` be updated in Phase 1 or Phase 2? | **Phase 1.** Without it, `/goals` shows "No goals found" after YAML is added. Rewrite `_parse_goals_index()` → `_parse_goals_yaml()` using `yaml.safe_load_all()` (not regex). | Broken `/goals` after Phase 1 is a poor first impression. Structured YAML parsing is more reliable than regex. |
| 8 | Should the `type` field exist from day one? | **Yes.** `type: outcome \| habit \| milestone` (PRD §7). Costs nothing; prevents schema migration; unblocks F13.10, F13.11, UX §8.6. | Adding a field later requires migrating all existing entries. |
| 9 | What about Habits & Streaks? | **Deferred to Phase 2.** `type: habit` is in the schema now. Habit-specific mechanics (cadence, streaks, implementation intentions) warrant their own design spec. | F13.11 has distinct mechanics beyond this spec's scope. |

---

## Appendix A: GTD Mapping

| GTD Step | Artha Implementation | Status |
|----------|---------------------|--------|
| **Capture** | Email → OI creation at Step 8 | ✅ Working |
| **Clarify** | `next_action` field in `goals` YAML block | 🆕 This spec |
| **Organize** | `status` + `type` fields on goals (active/parked/done/dropped × outcome/habit/milestone) | 🆕 This spec |
| **Reflect** | Weekly Goal Review in Monday summary (LLM-generated) | 🆕 This spec |
| **Engage** | `/power` command picks highest-impact OI + Goal Check + coaching nudge at Step 8 | 🆕 This spec (v4.0 adds Goal Check; was zero goal awareness) |

## Appendix B: Domain-to-Goal Mapping Reality

| OI Domain | Count | Has Matching Goal? | Notes |
|-----------|-------|--------------------|-------|
| kids | 11 | ❌ | School forms, grades — operational, not strategic |
| finance | 6 | ❌ | Tax, cards, NRI status — operational |
| insurance | 3 | ❌ | Renewal, quotes — operational |
| home | 2 | ❌ | Ring cameras, devices — operational |
| digital | 2 | ⚠️ Weak | Gemini migration loosely maps to learning |
| health | 1 | ✅ | Pediatrics → tangential to health goals |
| comms | 1 | ❌ | Communication tasks — operational |

**Bottom line:** 88% of OIs are operational tasks with no goal alignment.
The system correctly handles this by using LLM inference (not schema fields)
to surface the ~12% of OIs that do advance goals.

## Appendix C: What Changed from v1.0 → v2.0 → v3.0

| Area | v1.0 | v2.0 | v3.0 | Rationale |
|------|------|------|------|-----------|
| YAML key name | `goals_index` | `goals` | `goals` | Matches `coaching_engine._extract_goals()` lookup |
| Markdown tables | "Human-readable display" | **LLM's primary data layer** | Same | LLM IS the engine. Tables are what it reasons from. |
| YAML sync | Script writes | LLM sync directive | **`goals_writer.py` deterministic writer** | LLMs unreliable at mechanical sync (Tech Spec §1.1.4). Follows `health_check_writer.py` pattern. |
| Goal type | Not addressed | Not addressed | **`type: outcome \| habit \| milestone`** | PRD §7 Goal Model. Unblocks F13.10, F13.11, UX §8.6. |
| Goal creation | Not addressed | Not addressed | **Creation directive + `goals_writer.py --create`** | F13.1 (P0), UX §8.1, UX-5. Conversational, not YAML editing. |
| Leading indicators | Not addressed | Not addressed | **`leading_indicators: []` stub** | Step 8m auto-discovery after 30 catch-ups (PRD §8.11). |
| Habits | Not addressed | Not addressed | **Deferred to Phase 2** (`type: habit` in schema now) | F13.11 has distinct mechanics. |
| OI→goal linking | `goal_id` field | LLM inference at Step 8 | Same | 88% of OIs have no goal. LLM can infer the ~12%. |
| Weekly review | `goal_review.py` | LLM on Sundays | **LLM in existing Monday weekly summary** | One weekly cadence (UX-1). No separate trigger. |
| Coaching | New call site | LLM at Step 19b | **Engine at Step 8 + LLM narrative at Step 19b** | ACT-RELOADED E16; engine handles deterministic strategy/suppression. |
| Auto-park | `ignore_count` | PAT-003b pattern | Same | Data-driven, no interaction tracking. |
| Parked fields | On every goal (null) | On every goal (null) | **Conditional — only when status: parked** | Cleaner schema, less noise. |
| Step 3 location | `workflow/process.md` | `workflow/process.md` | **`config/Artha.core.md`** (correct file) | Step 3 lives at lines 222–250 of Artha.core.md, not process.md. |
| `goals_view.py` phase | Phase 2 | Phase 2 | **Phase 1** | Without update, `/goals` shows "No goals found" after schema change. |
| `is_null` operator | 5 lines | 5 lines | **5 lines, defensive** | Handles None, "", "null", and missing keys (LLM data hazard). |
| FR-13 coverage | Unstated | Unstated | **Explicit matrix** (§3.10) | 7 addressed, 3 enabled, 8 deferred with rationale. |
| Implementation | 5 phases, ~480 LOC | 3 phases, ~170 LOC | **2 phases, ~210 LOC (40 new + 170 modified)** | One new deterministic writer. All reasoning stays in prompts. |

### What Changed v3.0 → v4.0 → v5.0

| Area | v3.0 | v4.0 | v5.0 | Rationale |
|------|------|------|------|----------|
| Goal influence | Passive scoreboard | Gravitational field | Same | Goals shape ONE THING, `/power`, Step 8 |
| ONE THING scoring | Pure U×I×A | +1 Impact bonus | **Tie-breaker on equal U×I×A** | Bonus was too rigid; tie-breaker respects operational urgency |
| Work OS boundary | All goals in `goals.md` | Same | **Personal in `goals.md`, work in `state/work/work-goals.md`** | `/work` must not read personal state (commands.md, skills.yaml, artha-work.md) |
| OI→goal linkage | LLM inference, no persistence | `linked_ois: []` persisted at Step 8 | **Ephemeral reasoning only in Phase 1** | Persisting ~12% linkage creates churn; defer until value proven |
| Sprint model | Replace top-level `sprints:` | Same | **Coexist: top-level `sprints:` preserved for Step 3 compat** | Step 3 calibration reads `goals.md → sprints`; silent regression if removed |
| Bootstrap parking | Azure cert → active | Azure cert → parked immediately | **Azure cert → active (preserve current meaning)** | Migration should not reframe user intent; first review decides |
| `scorecard_view.py` | Not mentioned | Not mentioned | **Explicit migration** | Also parses `goals_index`; would break silently |
| `state/templates/goals.md` | Not mentioned | Not mentioned | **Updated to v2.0 schema** | Template must match live schema |
| Test coverage | 3 test files | Same | **+3 test files** (scorecard, boundary, sprint compat) | Expanded bar for contract migration |
| Downstream drift | Acknowledged goals_view.py | Same | **Framed as contract migration across all consumers** | Schema change is a migration, not just an enhancement |

## Appendix D: Architecture Alignment Summary

| Artha Principle | How this spec adheres |
|----------------|----------------------|
| "Claude Code IS the application" (Tech Spec §1.1) | Weekly review, goal creation, OI inference — all LLM reasoning via prompt directives. |
| "Zero custom code is the target" (Tech Spec §1.1.4) | One new script: `goals_writer.py` (~40 LOC) — deterministic YAML writer for the canonical "LLM proves unreliable" case. |
| "Scripts only for proven failure points" (Tech Spec §1.1.4) | Dual-layer YAML sync is mechanical data sync, not reasoning. `health_check_writer.py` proves this pattern. |
| P3 Goals above tasks (PRD) | Goals now have structured data, weekly review, coaching integration, creation path. OIs are inferentially linked. **v5.0: goals shape ONE THING scoring (tie-breaker on equal U×I×A) and `/power` triage.** |
| P6 Earned Autonomy (PRD) | Auto-park is recommendation only. Coaching is dismissible. `goals_writer.py` only writes what the LLM (confirming with user) tells it to write. |
| UX-1 Silence is default (UX Spec) | Goal review invisible except in weekly summary. Daily: GOAL PULSE + Goal Heartbeat (conditional, silent when on track) + coaching nudge if applicable. No separate Sunday trigger. |
| UX-3 Progressive disclosure (UX Spec) | Briefing shows goal pulse → weekly summary shows full review → `/goals` shows scorecard → `state/goals.md` has raw data. |
| UX-5 Conversation, not configuration (UX Spec) | Goal creation via natural conversation (§3.8). Never edit YAML directly. |
| ACT-RELOADED E16 (PRD) | Coaching engine at Step 8 (deterministic) + LLM narrative at Step 19b. Engine decides what; LLM decides how. |
