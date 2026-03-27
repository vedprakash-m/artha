# Skills Reloaded — Goal Traction Loop

**Spec ID:** SKILLS-RELOADED v2.2  
**Author:** Artha Enhancement Pipeline  
**Date:** 2026-03-26  
**Revised:** 2026-03-26 (incorporated architectural + product-shape review)  
**Status:** Proposed  
**Depends on:** None  
**Blocked by:** None  
**Estimated LOC:** ~210 (modifications to 5 existing scripts + 3 config files + 1 new shared lib)

---

## 0. Design Principles

This spec adheres to Artha's core architecture:

- **"Claude Code IS the application"** (Tech Spec §1) — no new scripts for
  reasoning work Claude already does. Scripts only where deterministic writes
  are needed.
- **"Zero custom code is the target"** (Tech Spec §4) — extend existing
  scripts (`health_check_writer.py`, `skill_runner.py`, `eval_runner.py`),
  don't create new ones.
- **"State lives in Markdown/YAML"** — one file per concern, no shadow caches.
- **P6 Earned Autonomy** (PRD) — the system proposes, the human decides.
- **UX-1 Silence is default** — skill hygiene is invisible unless
  something is broken. User-facing value surfaces through Goal Pulse,
  ONE THING, and coaching — not a side dashboard.
- **P3 Goals above tasks** (PRD) — every skill must have a clear role
  relative to goal traction. Features without a goal connection are
  deprioritized.

---

## 1. Problem Statement

Artha processes 25+ catch-ups but has no feedback loop measuring whether the
information it delivers actually moves the user's goals forward. The system
produces briefings, but cannot answer: "Did this briefing improve decision
quality, goal traction, or help avoid regret?" Without this signal, Artha
cannot improve — it treats a briefing full of ignored noise the same as one
that triggered 3 urgent actions.

Specifically:

1. **No engagement measurement.** There is no metric tracking what percentage
   of briefing items result in user-initiated OIs, user corrections, or
   user-directed state changes. The `signal_noise_ratio` field referenced by
   briefing adapter rule R2 is **never populated** — `grep -i signal_noise
   state/health-check.md` returns zero results. R2 is therefore dead.

2. **Skills produce zero-value output with no tracking.** 5 of 18 skills
   consistently produce useless output:
   - `uscis_status`: returns `{}` (empty)
   - `financial_resilience`: returns "insufficient_data" error
   - `mental_health_utilization`: status=failed
   - `work_background_refresh`: status=failed
   - `king_county_tax`: returns data but property tax check is stale/redundant
   
   These run every catch-up, consume time (~1.4s total wall clock per run),
   and inject misleading "changed=True" signals into the pipeline. No
   per-skill health tracking exists to detect persistent failure.

3. **Briefing adapter R2 rule is inactive.** The rule says "if
   `signal_noise_ratio < 30%` in ≥7 of last 10 runs → suppress info-tier
   domain items." But `signal_noise_ratio` is never computed or stored.
   The adaptive briefing system is designed but non-functional for its most
   important rule.

4. **`skills_metrics.json` is ephemeral and underutilized.** `skill_runner.py`
   writes timing metrics to `tmp/skills_metrics.json` (timestamps + per-skill
   wall clock). Only 2 entries exist. Step 18 clears `tmp/*.json` each session,
   so timing history is wiped. The data is not read by any other component.

5. **Health-check run entries are not structured.** `Artha.core.md` Step 16
   defines a YAML `catch_up_runs:` schema with `signal_noise`, `domain_hits`,
   and other fields. But actual entries in `state/health-check.md` are freeform
   Markdown (`### date (format)` + `- key: value` bullets). The
   `_load_catch_up_runs()` parser in `briefing_adapter.py` looks for YAML
   frontmatter — which is never populated with per-run data. This format
   mismatch blocks R2 and all future adaptive rules.

### What gets worse without this fix

- No way to know if Artha is improving or regressing in usefulness
- Failed skills run silently every catch-up, wasting time + polluting signals
- R2 adaptive compression never activates (no signal:noise data)
- No basis for informed decisions about which skills to keep/disable/fix

---

## 2. Assumptions (Tested)

| # | Assumption | Test | Result | Impact |
|---|-----------|------|--------|--------|
| B1 | `signal_noise_ratio` is never populated | `grep -i signal_noise state/health-check.md` | **Confirmed: zero results** | R2 rule is dead — must be wired up |
| B2 | Skills cache tracks `changed` and `status` per skill | Dumped all 18 entries from `skills_cache.json` | **Confirmed: each entry has `last_run`, `current.status`, `changed`** | Can build health tracking on existing structure |
| B3 | 2 skills consistently fail | Checked all 18 skills | **Confirmed: `mental_health_utilization` and `work_background_refresh` status=failed** | Need per-skill failure counter |
| B4 | 3 more skills return useless data | Inspected `current.data` for each | **Confirmed: `uscis_status` returns `{}`, `financial_resilience` returns insufficient_data, `king_county_tax` returns stale data** | Need "zero-value" detection beyond status=failed |
| B5 | `skills_metrics.json` exists and has useful structure | `ls -la tmp/skills_metrics.json` + parsed content | **Confirmed: 2 entries with per-skill timing** | Can extend, not replace |
| B6 | OIs are created during catch-up (not just from external sources) | Checked health-check `open_items_added` entries | **Confirmed: 7 of 7 logged catch-ups show OI additions** | OI creation rate is a viable proxy for "action taken" |
| B7 | Briefing adapter R2 checks `signal_noise` from health-check runs | Read `_r2_low_signal_noise()` source | **Confirmed: reads `r.get("signal_noise")` from catch-up runs** | Must write `signal_noise` to health-check |
| B8 | Catch-up run entries are freeform Markdown, not YAML | Read `state/health-check.md` | **Confirmed: `## Catch-Up Run History` with `### date` + `- key: value` entries, not YAML `catch_up_runs:` array** | `_load_catch_up_runs()` cannot parse current entries — format must be fixed |
| B9 | `skills_metrics.json` is ephemeral (in tmp/) | Checked path in `skill_runner.py` | **Confirmed: `ARTHA_DIR / "tmp" / "skills_metrics.json"` — cleared by Step 18, not synced** | Timing data lost each session — health counters must live in `state/` |
| B10 | Briefing adapter is not called directly by scripts | `grep -rn 'BriefingAdapter' scripts/*.py` | **Confirmed: only `action_bridge.py` mentions it (in a comment)** | BriefingAdapter is LLM-consumed, not script-invoked — adaptations are instructions, not code |
| B11 | `health_check_writer.py` is the single deterministic writer for health-check | Read source: CLI flags for `--last-catch-up`, `--email-count`, `--mode`, `--domains-processed` | **Confirmed: atomic POSIX write with lock** | All new health-check fields must be added as CLI flags here — no parallel write paths |
| B12 | `eval_runner.py` exists with `--perf`, `--accuracy`, `--freshness` | Read source: 3 analysis modules, reads metrics + health-check | **Confirmed: 400 LOC, no skill health awareness** | Extend this, don't create `eval_view.py` |
| B13 | `eval_view.py` does not exist | `ls scripts/eval_view.py` | **Confirmed: not found** | No file to extend — add to `eval_runner.py` instead |
| B14 | `domain_hit_rates` already tracks per-domain signal quality in Step 16 | Read `Artha.core.md` Step 16 schema | **Confirmed: `{routed_total, extracted_total, rate_pct, last_alert}` per domain with <60% alert** | Use existing per-domain hit rates rather than inventing aggregate action rate |
| B15 | Step 19 calibration captures user corrections | Read `config/observability.md` | **Confirmed: flags contradictions, notes uncertainty, corrections feed back** | User corrections are the purest engagement signal — must include in metric |

---

## 3. Design

### 3.1 Core Principle: Measure → Detect → Adapt

The effectiveness loop has three components:

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   MEASURE    │────▶│   DETECT     │────▶│   ADAPT      │
│ Engagement   │     │ Skill Health │     │ Briefing     │
│ per catch-up │     │ per skill    │     │ Compression  │
└──────────────┘     └──────────────┘     └──────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
  health-check.md    skills_cache.json    briefing_adapter
  signal_noise        health counters      R2 activates
  per-domain hits     zero_value_count     domain suppression
```

No new scripts. No shadow state files. Three existing files are extended:
`health_check_writer.py`, `skill_runner.py`, `eval_runner.py`.

### 3.1.1 Skill Classification Taxonomy

Every skill has a role relative to goal traction. Without this taxonomy,
the system improves cleanliness but doesn't become a goal engine.

| Class | Definition | Surfacing rules | Examples |
|-------|-----------|-----------------|----------|
| **Goal-bearing** | Directly feeds data into Goal Pulse, ONE THING selection, or coaching timing | Influences goal review, weekly summaries, `/power` prioritization. Degradation triggers Goal Pulse warning. | `bill_due_tracker`, `school_calendar`, `passport_expiry`, `visa_bulletin` |
| **Operational** | Provides time-sensitive or safety-critical signals not tied to a specific goal | Surfaces only when materially urgent (new alert, threshold crossed). Silent when stable. | `noaa_weather`, `nhtsa_recalls`, `credit_monitor`, `home_device_monitor` |
| **Background integrity** | Infrastructure health checks; user never sees output unless broken | Surfaces only on failure or sustained degradation. Never in briefing body. | `uscis_status` (when no active case), `work_background_refresh`, `king_county_tax` |

**Classification in `config/skills.yaml`:**
```yaml
bill_due_tracker:
  class: goal-bearing     # goal-bearing | operational | background
  priority: P1
  cadence: every_run
```

Default if unset: `operational`. Classification drives:
- Which skills appear in Goal Pulse vs. `/health` vs. nowhere
- R7 cadence reduction aggressiveness (background > operational > goal-bearing)
- Whether degradation triggers a goal-level warning or only a system note

### 3.2 Engagement Rate Measurement

**Why "Action Rate" is wrong.** The v1 definition — `(OIs created + state
changes) / domain sections` — conflates system extraction with user action.
When Step 7 extracts "Trisha's report card is due March 30" and creates
OI-042, that's *Artha acting*, not the user responding. And "domain sections"
measures Artha's verbosity, not signal quality — a briefing with 9 domains
where 3 had genuine urgency shouldn't score 0.33 when 3/3 urgent items were
acted on.

**Revised definition — Engagement Rate:**

```
Engagement Rate = (user_ois + user_corrections) / items_surfaced
```

| Component | What it measures | Source |
|-----------|-----------------|--------|
| `user_ois` | OIs explicitly created by the user during or after the briefing (not auto-extracted in Step 7) | `source: user` field in OI schema (see prerequisite below) |
| `user_corrections` | User corrects a finding ("that's wrong", "ignore X", "add this") | Step 19 calibration — correction_count |
| `items_surfaced` | Alerts generated at P0/P1/P2 severity during domain processing | Running counter tracked during Steps 7–8 as each alert is added to the briefing buffer |

**Why no content-hash diff for state changes.** The v1 draft included
`user_state_changes` via SHA-256 diff of `state/*.md` files. This is
mechanically clever but architecturally wrong: it introduces hidden
complexity, ambiguous attribution (did the user or the pipeline change
that file?), and false positives in a system where state is updated
throughout the workflow. The Artha principle is that scripts handle
deterministic writes with explicit contracts — ad hoc diffing across
state files violates that. `user_ois` and `user_corrections` are clean
signals through existing deterministic seams.

**Why this is better:**
- Numerator measures *human engagement*, not pipeline extraction
- Denominator measures *items we asked the user to care about*, not domains
  that existed
- Aligns with UX-1 ("every line must justify its presence") — we measure
  whether the lines we surfaced triggered human response

**Target range:** 25–50% engagement rate. Not 100% — some items provide
valuable awareness without triggering action ("all passports valid" is
worth hearing even if no OI results).

**Metric hierarchy.** Engagement rate is the *diagnostic* metric — useful
for internal health but not the product truth. The full hierarchy:

| Level | Metric | What it answers | When available |
|-------|--------|----------------|----------------|
| Diagnostic | Engagement rate | Did the user react to this briefing? | Phase 2 (day 2) |
| Behavioral | Action activation rate | Did the briefing trigger concrete next steps? | Phase 2 (user_ois / items_surfaced) |
| Outcome | Goal movement rate | Are goals with live leading indicators progressing? | Phase 3+ (requires goals-reloaded) |
| Trust | Correction-adjusted usefulness | Is the user correcting Artha less over time? | Phase 3 (correction_count trend) |

Engagement rate is the internal signal that powers R2 compression and
skill health. Goal movement rate is the product truth that surfaces in
Goal Pulse and weekly reviews. The spec implements the diagnostic layer
now; outcome metrics arrive when goals-reloaded lands.

**R2 compatibility:** `signal_noise = engagement_rate` — the field name
matches what `_r2_low_signal_noise()` already reads via `r.get("signal_noise")`.

**Prerequisite — deterministic OI source tracking:** Claude cannot reliably
maintain a running counter distinguishing system-created OIs from user-created
OIs across a multi-minute reasoning session. This is exactly the class of
mechanical bookkeeping where LLMs are unreliable. Instead, make the
distinction structural:

- Add `source: system | user` field to OI schema in `open_items.md`.
  One-time migration (~5 LOC change to Step 7b OI creation instructions).
- Step 7b auto-extracted OIs get `source: system`.
- Step 19 user-requested OIs get `source: user`.
- `health_check_writer.py` accepts `--user-ois N --system-ois N` as
  separate flags. Claude observes during the session (easy); the script
  records the value (deterministic).

**Prerequisite — items_surfaced counter:** Do NOT post-parse the rendered
briefing Markdown for emoji severity markers (fragile). Instead, track
`items_surfaced` as a running counter during Steps 7–8 as each alert is
generated. Pass the count to `health_check_writer.py` at Step 16.

**Per-domain engagement — deferred to Phase 3.** The existing
`domain_hit_rates` system in Step 16 tracks `{routed_total, extracted_total,
rate_pct}` per domain. Adding per-domain `engagement_pct` requires
attributing user OIs to specific domains — when a user says "add a reminder
for the Kia lease," does that belong to `vehicle`, `finance`, or
`calendar`? This multi-dimensional classification is noisy with small sample
sizes. Start with **aggregate engagement rate only**. Per-domain engagement
arrives in Phase 3 after 30+ catch-ups prove the aggregate metric has
variance and is meaningful. R2 can still activate on aggregate; per-domain
R2 becomes surgical once per-domain data is trustworthy.

**Implementation — no new script.** Claude already reads `open_items.md`
in Step 0 and again during Step 8. At Step 16, pass the computed values
as CLI flags to the existing `health_check_writer.py`:

```bash
python3 scripts/health_check_writer.py \
  --last-catch-up "$TIMESTAMP" \
  --email-count $N \
  --mode "$FORMAT" \
  --domains-processed "$DOMAINS" \
  --signal-noise 0.33 \
  --user-ois 1 \
  --system-ois 3 \
  --items-surfaced 6 \
  --correction-count 1
```

This maintains `health_check_writer.py` as the single deterministic writer
for health-check state. No parallel write paths. No snapshot/diff scripts.

**Prerequisite — structured run entries:** Run entries must be stored as
structured YAML, not freeform Markdown. See §3.2.1.

### 3.2.1 Structured Run Storage

**Problem:** The current `_update_frontmatter()` in `health_check_writer.py`
handles scalar key-value pairs via regex upsert between `---` markers. It
cannot handle YAML arrays. Putting `catch_up_runs:` as a growing array inside
frontmatter introduces two risks: (1) after 100 catch-ups, 300+ lines of
YAML in frontmatter makes the file fragile; (2) appending to a YAML array
via regex is error-prone — one malformed entry corrupts the entire array.

**Solution: separate `state/catch_up_runs.yaml` file.** One file per concern.
Simple append-only YAML list. No frontmatter surgery.

- `health_check_writer.py` creates/appends to `state/catch_up_runs.yaml`
  atomically (same lock + tempfile + os.replace pattern) alongside the
  existing `health-check.md` update.
- `briefing_adapter.py` `_load_catch_up_runs()` reads
  `state/catch_up_runs.yaml` directly (5-line change).
- `_update_frontmatter()` continues handling scalars only — no rewrite.
- Freeform Markdown run history in `health-check.md` body is preserved
  as-is for human readability.
- **Retention policy:** Keep the last 100 entries in `catch_up_runs.yaml`.
  On each write, if entry count exceeds 100, truncate oldest entries.
  At ~1.3 catch-ups/day, 100 entries = ~77 days of history — more than
  enough for any 10-run or 30-run window analysis.
- **Null behavior:** If `items_surfaced == 0` (no alerts generated),
  `signal_noise` is stored as `null`, not `0.0`. R2 skips null entries
  in its window — a catch-up with no alerts is not a low-engagement
  catch-up, it's a no-signal catch-up.

```yaml
# state/catch_up_runs.yaml — machine-parseable run history
# Written by health_check_writer.py; read by briefing_adapter.py
- timestamp: "2026-03-27T09:00:00Z"
  signal_noise: 0.33
  user_ois: 1
  system_ois: 3
  items_surfaced: 6
  correction_count: 1
  briefing_format: standard
  email_count: 30
  domains_processed: [kids, finance, calendar]
- timestamp: "2026-03-26T17:00:00Z"
  signal_noise: 0.25
  user_ois: 0
  system_ois: 2
  items_surfaced: 3
  correction_count: 0
  briefing_format: flash
  email_count: 49
  domains_processed: [kids, insurance, finance, calendar, home, comms]
```

### 3.3 Per-Skill Health Tracking (Unified Cache)

**No `state/skill_health.yaml`.** The system already has
`tmp/skills_cache.json` with per-skill `status`, `data`, `changed`,
`last_run`, and `previous`. Creating a second file in `state/` duplicates
data with a different lifecycle and introduces a coordination problem.

**Instead: promote and extend `skills_cache.json`.**

1. **Move** `tmp/skills_cache.json` → `state/skills_cache.json` (persists
   across sessions, survives Step 18 cleanup, syncs via OneDrive, backed up)
2. **Add health counters** to each skill entry alongside existing fields:

```json
{
  "uscis_status": {
    "last_run": "2026-03-26T17:00:00Z",
    "current": {"name": "uscis_status", "status": "success", "data": {}},
    "previous": null,
    "changed": false,
    "health": {
      "total_runs": 25,
      "success_count": 25,
      "failure_count": 0,
      "zero_value_count": 25,
      "last_success": "2026-03-26",
      "last_failure": null,
      "last_nonzero_value": null,
      "consecutive_zero": 25,
      "last_wall_clock_ms": 45,
      "maturity": "trusted",
      "classification": "degraded"
    }
  }
}
```

**One file, one schema, one lifecycle.** Health counters are updated in the
same write as the cache update — no second write path. The `last_wall_clock_ms`
field folds essential timing data into the cache, eliminating the need to
keep `skills_metrics.json` in a separate lifecycle.

**Health classification rules:**
- `warming_up`: fewer than 5 runs recorded (insufficient sample — no
  adaptation rules fire, no classification displayed to user)
- `healthy`: success_rate ≥ 80% AND had nonzero value in last 10 runs
- `degraded`: success_rate ≥ 80% BUT `consecutive_zero >= 10` (succeeds but useless)
- `broken`: success_rate < 50% over last 10 runs
- `disabled`: `enabled: false` in `config/skills.yaml`

**Maturity semantics.** The system must not classify a skill as truly
broken or let adaptation rules fire before the sample is meaningful:

| Maturity | Condition | Behavior |
|----------|-----------|----------|
| `warming_up` | `total_runs < 5` | No classification. No R7. No display in skill health table (except `/health --verbose`). |
| `measuring` | `5 ≤ total_runs < 15` | Classification assigned. R7 cadence reduction eligible. R7 disable prompts suppressed. |
| `trusted` | `total_runs ≥ 15` | Full behavior: R7 prompts, R2 influence, `/eval skills` trend data reliable. |

This aligns with P6 (earned autonomy) — the system earns the right to
adapt by accumulating evidence, not by guessing from sparse data.

**Recovery rules (self-healing):**
- If `classification == degraded` and current run is non-zero-value:
  reset `consecutive_zero = 0`, reclassify to `healthy` immediately.
- If `classification == broken` and last 3 consecutive runs succeed:
  promote to `healthy`.
- If R7 reduced cadence and skill recovers (non-zero value): restore
  original cadence from `config/skills.yaml` on next non-zero run.

Without recovery rules, a skill degraded at catch-up 10 stays degraded
at catch-up 100 even if it's been returning good data since catch-up 30.
The system must be self-healing.

**Integration:** ~50 LOC added to `skill_runner.py`:
- Update `CACHE_FILE` path from `tmp/` to `state/`
- Add `_is_zero_value()` + `_is_stable_value()` called after each skill
- Add `_update_health_counters()` called during cache write
- All wrapped in try/except — health tracking failure is non-blocking

### 3.4 Zero-Value and Stable-Value Detection

A skill returning `status: success` but producing no actionable data is a
"zero-value" run. But not all unchanged data is zero-value — some skills
legitimately return unchanged data that provides assurance ("all passports
valid"). The taxonomy:

| Category | Detection | Classification |
|----------|-----------|----------------|
| **Empty** | `data == {}` or `data is None` | zero-value |
| **Error-masked** | `data.error` exists or `data.status in ("insufficient_data", "error")` | zero-value |
| **Stable** | `data` equals previous `data` for N consecutive runs AND no alerts fired | healthy (candidate for cadence reduction) |
| **Stale-changing** | `changed == True` but content is semantically identical (e.g., timestamp jitter in otherwise static data) | degraded (false-positive signals) |
| **Genuine "all-clear"** | `changed == False`, alerts empty, but `data` contains real values (e.g., `{passports: [{status: "valid"}]}`) | healthy |

**Implementation:** Two functions in `skill_runner.py`:

```python
def _is_zero_value(skill_name: str, result: dict, prev_result: dict | None,
                   skills_config: dict) -> bool:
    """True if the skill produced no actionable data.
    
    Checks skill-specific zero_value_fields override first, then
    falls back to generic detection.
    """
    data = result.get("data")
    if data is None or data == {}:
        return True
    if isinstance(data, dict):
        if data.get("error") or data.get("status") in ("insufficient_data", "error"):
            return True
    # Per-skill override: only check specified fields for value
    skill_cfg = skills_config.get(skill_name, {})
    zero_fields = skill_cfg.get("zero_value_fields")
    if zero_fields and isinstance(data, dict):
        return all(not data.get(f) for f in zero_fields)
    return False
```

Skill-specific overrides in `config/skills.yaml` (optional):

```yaml
king_county_tax:
  zero_value_fields: ["tax_amount"]  # only check these for value
```

This keeps the generic logic clean while allowing per-skill tuning without
code changes. `_is_zero_value()` checks the override first, falls back to
generic `data == {}` check.

```python
def _is_stable_value(result: dict, prev_result: dict | None) -> bool:
    """True if data is identical to previous run (not zero, just unchanged)."""
    if prev_result is None:
        return False
    curr_data = result.get("data")
    prev_data = prev_result.get("data")
    # Compare payloads, ignoring timestamp fields
    return _normalize(curr_data) == _normalize(prev_data)
```

**Cadence reduction for stable-value skills:** If a skill has
`consecutive_stable >= 10` (data unchanged for 10 runs), auto-suggest
cadence reduction in the `/health` display:
- `every_run` → suggest `daily`
- `daily` → suggest `weekly`

This is a suggestion only (displayed in Skill Health table), not an
autonomous change — consistent with P6 (earned autonomy).

### 3.5 Adaptive Skill Cadence (R7 — Real Rule)

The v1 spec proposed R7 as "informational, no suppression." That's timid.
If Artha *knows* a skill returns empty for 25 consecutive runs, it should
act — within the bounds of earned autonomy (P6).

**R7 behavior:**

| Consecutive Zeros | Action |
|-------------------|--------|
| 10 | Auto-reduce cadence (`every_run` → `daily`, `daily` → `weekly`) with disclosure in briefing footer (human language, see §3.6.1) |
| 20 | Surface one-time prompt in briefing: "[skill description] has been quiet for a while — disable it? [yes / keep running]" |
| User responds "yes" | Set `enabled: false` in `config/skills.yaml` with comment `# disabled by R7 — re-enable when needed` |
| User responds "keep" | Set `suppress_zero_prompt: true` in skill config — no further prompts |

**Cadence reduction is autonomous; disabling requires human approval.**
This is a safe split — reducing frequency is low-risk and reversible;
disabling a P0 skill (e.g., `uscis_status` during a future filing) is not.

**P0 exception:** P0 skills (`uscis_status`, `visa_bulletin`) are NEVER
auto-disabled or cadence-reduced. R7 only prompts.

**Implementation — R7 cadence logic lives in `skill_runner.py`**, not
`briefing_adapter.py`. The briefing adapter is about adjusting *briefing
format parameters* (format, caps, calibration, coaching). R7 is about
*skill execution cadence* — it belongs in the executor.

`skill_runner.py` already implements `should_run()` with cadence checks.
R7 extends `should_run()` directly: if `health.consecutive_zero >= 10`
and skill is not P0, skip this run (effectively reducing cadence). Log
the skip reason to `health.r7_skips`.

```python
# In skill_runner.py should_run():
def should_run(skill_name: str, config: dict, cache: dict) -> bool:
    """Cadence check, now extended with R7 health-aware skipping."""
    # ... existing cadence logic (every_run, daily, weekly) ...
    
    # R7: health-aware cadence reduction (P1/P2 only)
    health = cache.get(skill_name, {}).get("health", {})
    priority = config.get("priority", "P1")
    if priority != "P0" and health.get("consecutive_zero", 0) >= 10:
        configured_cadence = config.get("cadence", "every_run")
        reduced_cadence = _CADENCE_REDUCTION.get(configured_cadence)
        if reduced_cadence:
            return _cadence_elapsed(skill_name, reduced_cadence, cache)
    return True

_CADENCE_REDUCTION = {
    "every_run": "daily",
    "daily": "weekly",
}
```

The briefing adapter handles *display only* — reading
`state/skills_cache.json` to render cadence disclosures in the briefing
footer using human language (see §3.6.1). ~10 LOC in `briefing_adapter.py`
for the footer string, not the decision logic.

### 3.6 Skill Health in Briefing

When the `/health` command runs or catch-up includes the Connector Health
table, append a "Skill Health" section:

```markdown
### 🔧 Skill Health

| Skill | Health | Last Value | Consecutive Zeros | Action |
|-------|--------|-----------|-------------------|--------|
| uscis_status | ⚠️ degraded | Never | 25 | Now checks less often (was empty 25×) |
| mental_health_utilization | 🔴 broken | Never | N/A (all failures) | Fix or disable |
| financial_resilience | ⚠️ degraded | Never | 25 | Needs vault/config fix |
| king_county_tax | 🔄 stable | Mar 26 | 0 (stable×15) | Consider cadence: weekly |
| bill_due_tracker | ✅ healthy | Mar 25 | 0 | — |
```

**Display rules:**
- `/health` command: Full table, all skills
- Catch-up briefing: Only show broken/degraded skills (healthy suppressed)
- Flash mode: Only show broken skills (degraded suppressed too)

#### 3.6.1 User-Facing Language (No Rule Names)

Internal rule names (R2, R7, R8) appear in logs, `/eval` output, and this
spec. They NEVER appear in the briefing or user-facing prompts. Mapping:

| Internal | User-facing briefing language |
|----------|------------------------------|
| R2 active | "Briefing simplified — recent sessions show low engagement" |
| R7 cadence reduced | "[Skill description] now checks less often (no new data recently)" |
| R7 disable prompt | "[Skill description] has been quiet for a while — disable it?" |
| R8 meta-alarm | "Recent briefings are generating little action — want to narrow focus?" |

This aligns with UX-3 (progressive disclosure) and UX-8 (minimal cognitive
tax). The user sees consequences, not implementation.

### 3.7 Adaptive Briefing Compression (R2 Activation)

With `signal_noise` now populated in health-check run entries, the existing
`briefing_adapter.py` R2 rule activates automatically:

```python
def _r2_low_signal_noise(runs: list[dict]) -> str | None:
    """R2: if signal_noise_ratio < 30% in ≥7 of last 10 runs → suppress info tier."""
    window = _last_n(runs, _WINDOW)
    low_count = 0
    for r in window:
        snr = r.get("signal_noise", r.get("signal_noise_ratio"))
        # NOW POPULATED by health_check_writer.py --signal-noise flag
```

**Per-domain R2 (Phase 3 — deferred):** With per-domain engagement tracked
in `domain_hit_rates`, R2 could suppress specific low-engagement domains
rather than applying a blanket `domain_item_cap`. However, per-domain
attribution requires reliable domain-level engagement data (≥30 catch-ups).
Phase 1–2 uses aggregate engagement rate for R2; Phase 3 upgrades to
per-domain once the data is trustworthy:

```python
# R2 Phase 3: per-domain compression (NOT Phase 1)
for domain, hits in domain_hit_rates.items():
    if hits.get("engagement_pct", 1.0) < 0.10 and domain not in P0_P1_DOMAINS:
        cfg.suppressed_sections.append(domain)
```

**Safety rails (unchanged):**
- P0/P1 domains are NEVER suppressed (immigration, health, finance deadlines)
- R2 only activates after 10 catch-ups with `signal_noise` data (cold-start safe)
- R2 is disclosed in briefing footer using human language (§3.6.1)
- User can override: `/catch-up deep` always shows full domain output

### 3.7.1 Meta-Regression Alarm (R8 — New Rule)

The effectiveness loop must monitor *itself*. Without this, engagement can
drop to zero and nobody notices.

**R8 behavior:** If engagement rate < 15% for 7 of last 10 runs, surface
a one-time prompt in the briefing:

> "⚠️ Recent briefings are generating little action. Would you like to:
> [Narrow focus] | [Trim quiet domains] | [Switch to flash-only] |
> [Keep current setup]"

R8 fires at most once per 14-day window. User response is logged.
`Keep current setup` suppresses R8 for 30 days.

**Implementation:** ~15 LOC in `briefing_adapter.py`, reading
`state/catch_up_runs.yaml` (same data source as R2).

### 3.8 Effectiveness Dashboard

**`/eval effectiveness` — Claude-rendered (no new Python):**

Engagement trends are a small number of data points (10 runs) from
`state/catch_up_runs.yaml`. Claude reads this file directly and narrates
better than a script formats. Rendering logic lives in the `/eval
effectiveness` command prompt instruction, not in Python. This saves ~40
LOC and keeps the eval surface clean: scripts for data that needs
computation (percentiles, trends), Claude for narrative.

```markdown
## Artha Effectiveness — Last 10 Catch-ups

| Date | Format | Engagement | User OIs | Corrections | Items Surfaced | Skills Broken |
|------|--------|-----------|----------|-------------|----------------|---------------|
| 3/26 | flash  | 0.33      | 1        | 0           | 3              | 2             |
| 3/26 | std    | 0.50      | 2        | 1           | 6              | 2             |
| 3/20 | std    | 0.40      | 2        | 0           | 5              | 2             |

**Trends:**
- Mean engagement rate: 0.41 (target: 0.25–0.50) ✅
- R2 compression: NOT ACTIVE (need 3 more runs with signal_noise)

**Recommendations:**
- Disable `mental_health_utilization` — 100% failure rate
- Disable `uscis_status` — no active case; re-enable when filing
- Reduce `king_county_tax` cadence to weekly — data unchanged 15 sessions
```

**`/eval skills` (`--skills` flag in `eval_runner.py`):**

Skill health requires computation (success rates, percentiles, counter
aggregation across 18 skills) — this is the right use of a Python script.

```markdown
## Skill Health — 18 Skills

| Skill | Health | Success% | Zero% | Last Value | Wall Clock | Cadence |
|-------|--------|---------|-------|-----------|-----------|---------|
| uscis_status | ⚠️ degraded | 100% | 100% | Never | 45ms | every_run → suggest daily |
| mental_health_utilization | 🔴 broken | 0% | — | Never | — | daily |
| bill_due_tracker | ✅ healthy | 100% | 72% | Mar 25 | 12ms | every_run |
| ...
```

### 3.9 MCP Tier 1 Compatibility

Step 4 has two tiers: MCP (`artha_run_skills`) and pipeline (`skill_runner.py`).
When skills run via MCP, `skill_runner.py` isn't invoked, so health tracking
must work for both paths:

- **Pipeline path:** `skill_runner.py` updates `state/skills_cache.json`
  with health counters directly (Phase 1).
- **MCP path:** The MCP `artha_run_skills` handler must also update
  `state/skills_cache.json` health counters after execution.

**Shared library:** Extract health-tracking functions into
`scripts/lib/skill_health.py` — a pure-function library importable by both
`skill_runner.py` and the MCP handler:

```python
# scripts/lib/skill_health.py
def is_zero_value(skill_name: str, result: dict, prev_result: dict | None,
                  skills_config: dict) -> bool: ...
def is_stable_value(result: dict, prev_result: dict | None) -> bool: ...
def update_health_counters(cache_entry: dict, is_zero: bool,
                          is_stable: bool) -> dict: ...
def classify_health(health: dict) -> str: ...
```

Both paths converge at the file level (`state/skills_cache.json`) and the
function level (`skill_health.py`). The MCP handler imports and calls
the same functions — no second implementation.

---

### 3.10 Phase Ownership — Deterministic Contracts

Each metric has exactly one pipeline phase that owns its computation and
one script that owns its persistence. If ownership is vague, the metric
will decay.

| Metric | Computed by | During phase | Persisted by | Written to |
|--------|------------|-------------|-------------|-----------|
| `items_surfaced` | Claude (running counter) | Steps 7–8 (Process) | `health_check_writer.py --items-surfaced` | `state/catch_up_runs.yaml` |
| `user_ois` / `system_ois` | Claude (reads `source` field) | Step 16 (Finalize) | `health_check_writer.py --user-ois --system-ois` | `state/catch_up_runs.yaml` |
| `correction_count` | Claude (observes Step 19) | Step 19 (Finalize) | `health_check_writer.py --correction-count` | `state/catch_up_runs.yaml` |
| `signal_noise` | Claude (= engagement_rate) | Step 16 (Finalize) | `health_check_writer.py --signal-noise` | `state/catch_up_runs.yaml` |
| Skill health counters | `skill_health.py` functions | Step 4 (Process) | `skill_runner.py` / MCP handler | `state/skills_cache.json` |
| OI `source` field | Claude (labels on creation) | Step 7b / Step 19 | Claude (writes to `open_items.md`) | `state/open_items.md` |

**Workflow file updates required:**
- `config/workflow/process.md`: Step 4 cache location; Step 7b `source: system` on auto-OIs; `items_surfaced` counter starts
- `config/workflow/reason.md`: Step 8 `items_surfaced` counter continues
- `config/workflow/finalize.md`: Step 16 emits all CLI flags to `health_check_writer.py`; Step 19 `source: user` on user-OIs; correction count

---

## 4. Implementation Plan

### Phase 1: Persistent Cache + Skill Health (Day 1)

The highest-ROI change: make the skills cache persistent and add health
counters. This unlocks R7 cadence reduction and `/eval skills` immediately,
with no dependency on health-check format changes.

| Step | File | Change | Risk |
|------|------|--------|------|
| 1.1 | `scripts/lib/skill_health.py` | **New file.** Extract pure functions: `is_zero_value()`, `is_stable_value()`, `update_health_counters()`, `classify_health()`. ~50 LOC shared library importable by both `skill_runner.py` and MCP handler. | **Low** — pure functions, no side effects, fully testable |
| 1.2 | `scripts/skill_runner.py` | Move `CACHE_FILE` from `tmp/skills_cache.json` to `state/skills_cache.json`. Add migration: if old path exists and new doesn't, move it. Import health functions from `lib/skill_health.py`. Call `update_health_counters()` after each skill run (wrapped in try/except). | **Medium** — path change affects any code reading the cache. Grep confirms only `skill_runner.py` reads/writes it; MCP `artha_run_skills` reads `tmp/` path → update needed. |
| 1.3 | `scripts/skill_runner.py` | Extend `should_run()` with R7 cadence reduction: if `health.consecutive_zero >= 10` and skill is not P0, apply reduced cadence. | **Low** — additive logic in existing function |
| 1.4 | `config/workflow/process.md` | Update Step 4 skill runner references to note new cache location. | **Low** — documentation |

**Why no `_bootstrap_health()`:** `skills_metrics.json` has 2 entries.
`skills_cache` has `current` and `previous` (at most 2 data points per skill).
Seeding `total_runs: 2, success_count: 2` is barely more informative than
starting at zero. After 10 real catch-ups (8 days), any bootstrapped data
is <20% of the total. The cold-start period is already gated by R2's 10-run
minimum. Accept the 8-day warm-up rather than adding 15 LOC of one-time
migration code.

**Verification:**
```bash
python3 scripts/skill_runner.py
python3 -c "import json; c=json.load(open('state/skills_cache.json')); print(c['uscis_status']['health'])"
# Expected: {'classification': 'degraded', 'consecutive_zero': 1, ...}
# (starts from zero, builds over subsequent runs)
```

**Delivers:** Broken/degraded skill detection. R7 auto-reduces cadence for
junk skills. `/health` shows skill health table immediately. No dependency
on health-check format changes.

**Rollback:** Move `state/skills_cache.json` back to `tmp/skills_cache.json`.
Existing code works with the original schema (ignores `health` sub-dict).

### Phase 2: Structured Runs + Signal:Noise (Day 2)

| Step | File | Change | Risk |
|------|------|--------|------|
| 2.1 | `scripts/health_check_writer.py` | Add CLI flags: `--signal-noise`, `--user-ois`, `--system-ois`, `--items-surfaced`, `--correction-count`. Write these fields to `state/catch_up_runs.yaml` (new file, append-only YAML list). Existing `health-check.md` frontmatter update continues for scalar fields only. | **Medium** — adds a second write target, but same lock + tempfile + os.replace pattern. `health-check.md` frontmatter writer is untouched. |
| 2.2 | `scripts/briefing_adapter.py` | Update `_load_catch_up_runs()` to read `state/catch_up_runs.yaml` instead of parsing `health-check.md` frontmatter. ~5-line change. | **Low** — swaps data source, same return type |
| 2.3 | `config/workflow/finalize.md` | Update Step 16 instructions: Claude computes signal_noise, user_ois, system_ois, items_surfaced, correction_count during Steps 8–19 and passes them as CLI flags to `health_check_writer.py`. | **Low** — documentation/instruction change |
| 2.4 | `config/workflow/process.md` | Add `source: system` to Step 7b OI creation template. Add `source: user` to Step 19 user-requested OI creation. | **Low** — OI schema extension |
| 2.5 | `config/workflow/finalize.md` | Update Step 19 instructions: after calibration, note the correction count for the `--correction-count` flag. | **Low** — coordination |

**Verification:**
```bash
python3 scripts/health_check_writer.py \
  --last-catch-up "2026-03-27T09:00:00Z" \
  --email-count 30 --mode standard \
  --domains-processed "kids,finance,calendar" \
  --signal-noise 0.33 --user-ois 1 --system-ois 3 \
  --items-surfaced 6 --correction-count 1
# Verify: state/catch_up_runs.yaml has new entry with all fields
```

**R2 auto-activates** after 10 catch-ups populate `signal_noise`. No changes
needed to R2 logic in `briefing_adapter.py`.

**Delivers:** R2 wakes up after 10 catch-ups. Engagement rate is computed
and stored. Aggregate signal:noise visible in `/eval effectiveness`.

### Phase 3: Eval Extension + Per-Domain + R8 (Day 3)

| Step | File | Change | Risk |
|------|------|--------|------|
| 3.1 | `scripts/eval_runner.py` | Add `analyze_skills()` function (~30 LOC). Reads `state/skills_cache.json` health counters. Renders skill health table, computes broken/degraded counts, cadence suggestions. Triggered by `--skills` flag. | **Low** — read-only view |
| 3.2 | `scripts/briefing_adapter.py` | Add R7 footer display (~10 LOC): read `state/skills_cache.json`, render R7 disclosure strings for skills with reduced cadence. Add R8 meta-regression alarm (~15 LOC): read `state/catch_up_runs.yaml`, check engagement < 15% for 7/10 runs. | **Low** — additive rules, non-blocking |
| 3.3 | `config/commands.md` | Document `/eval effectiveness` and `/eval skills` commands. | **Low** — docs only |
| 3.4 | `config/workflow/finalize.md` | Add instruction: "If any skills have `classification: broken` or `classification: degraded` in `state/skills_cache.json`, show Skill Health table after Connector Health in the briefing." | **Low** — additive display |

**Deferred to Phase 3+ (after 30 catch-ups):**
- Per-domain `engagement_pct` in `domain_hit_rates`
- Per-domain R2 surgical suppression

**Verification:**
```bash
python3 scripts/eval_runner.py --skills
# Expected: formatted table with skill health, cadence suggestions
```

---

## 5. Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Engagement rate metric doesn't capture all valuable briefing outcomes (awareness, peace of mind) | High | Medium | Engagement rate is a **lower bound**, not the full picture. Target 25–50%, not 100%. "All passports valid" is valuable without triggering an OI — that's why the target isn't 100%. |
| R2 | R2 compression suppresses a domain that later has urgent content | Low | High | P0/P1 priority domains are NEVER suppressed. R2 only affects low-engagement info-tier items. PAT-001/004 patterns fire independently of briefing compression. Explicit `/catch-up deep` overrides all compression. Per-domain R2 is surgical — doesn't blanket-suppress. |
| R3 | `_is_zero_value()` misclassifies a legitimate "no news is good news" skill run as zero-value | Medium | Medium | Zero-value requires `data == {}` (truly empty) or `data.error` present. Skills like `passport_expiry` returning `{passports: [{status: "valid"}]}` have real data — classified as `healthy`. Separate `_is_stable_value()` handles the "unchanged but valid" case correctly. |
| R4 | Moving `skills_cache.json` from `tmp/` to `state/` increases sync/backup size | Low | Low | File is ~20KB for 18 skills. Well within acceptable OneDrive sync overhead. Backup system already handles `state/` files. |
| R5 | Modifying `skill_runner.py` breaks skill execution | Medium | High | Changes are append-only: `_is_zero_value()`, `_is_stable_value()`, and `_update_health_counters()` called AFTER cache write succeeds. All wrapped in try/except — health tracking failure never blocks skill execution. Unit tests cover all detection rules. |
| R6 | `state/catch_up_runs.yaml` adds a second write target for health-check data | Medium | Low | Same lock + tempfile + os.replace atomic write pattern as `health-check.md`. File is append-only YAML list, ~2KB after 100 catch-ups. Freeform Markdown in `health-check.md` body is preserved as-is for human readability. No frontmatter surgery needed. |
| R7 | R7 auto-cadence-reduction changes behavior for P0 skills without user approval | Low | High | **P0 skills are exempt.** R7 only auto-reduces cadence for P1/P2 skills. P0 skills only get prompted at 20 consecutive zeros — never auto-modified. |
| R8 | `signal_noise` field name conflicts with future health-check schema changes | Low | Low | Field name matches existing R2 code expectation exactly (`r.get("signal_noise")`). No ambiguity. Documented in Artha.core.md Step 16 schema. |
| R9 | MCP path doesn't update skill health counters | Medium | Medium | Shared `scripts/lib/skill_health.py` library provides pure functions importable by both `skill_runner.py` and MCP handler. Both paths converge at the file level (`state/skills_cache.json`) and the function level. No second implementation. |
| R10 | R8 meta-regression alarm fires too aggressively or annoys user | Low | Low | R8 fires at most once per 14 days. User "keep current setup" suppresses for 30 days. Only triggers when engagement < 15% for 7 of 10 runs — a genuinely concerning signal. |

---

## 6. Success Criteria

| Metric | Baseline (today) | Target (4 weeks) | Measurement |
|--------|-------------------|-------------------|-------------|
| `signal_noise` populated in catch-up runs | 0 entries | 100% of catch-ups after Phase 2 | Count entries with `signal_noise` field in `state/catch_up_runs.yaml` |
| `catch_up_runs.yaml` has structured entries | 0 structured entries | All new entries are YAML | `_load_catch_up_runs()` returns non-empty list |
| R2 rule activation | Dead (never fires) | Evaluates correctly after 10 catch-ups | `BriefingAdapter.recommend().adaptive_adjustments` includes R2 status |
| Broken/degraded skills identified | Unknown (no tracking) | 2 broken, 3 degraded flagged in `/eval skills` | `state/skills_cache.json` health classification |
| User disables ≥1 zero-value skill based on data | 0 disabled | ≥1 skill disabled with data-backed reason | `config/skills.yaml` `enabled: false` with comment |
| Engagement rate computed per catch-up | N/A | Mean visible in `/eval effectiveness` | `state/catch_up_runs.yaml` entries |
| Catch-up wall-clock time saved (broken skills disabled) | ~1.4s for 18 skills | ~0.8s for 13 healthy skills | `state/skills_cache.json` `last_wall_clock_ms` trend |
| OI source tracking | No `source` field | 100% of new OIs have `source: system\|user` | `state/open_items.md` schema |

### Kill Criteria (abandon if)

- Engagement rate metric shows no variance (same number every catch-up) — metric is
  broken, not capturing real signal
- Skill health tracking causes `skill_runner.py` failures (>2 incidents in first week)
- User never consults effectiveness data (zero `/eval effectiveness` or `/eval skills`
  invocations after 4 weeks)

---

## 7. Files Changed

### New Files

| File | Size | Purpose |
|------|------|---------|
| `scripts/lib/skill_health.py` | ~50 lines | Shared pure-function library: `is_zero_value()`, `is_stable_value()`, `update_health_counters()`, `classify_health()`. Importable by both `skill_runner.py` and MCP handler. |
| `state/catch_up_runs.yaml` | ~0 (created on first run) | Machine-parseable append-only YAML list of catch-up run entries. Written by `health_check_writer.py`, read by `briefing_adapter.py`. |

### Modified Files
| File | Change Size | Purpose |
|------|------------|---------|
| `scripts/health_check_writer.py` | +30 lines | `--signal-noise`, `--user-ois`, `--system-ois`, `--items-surfaced`, `--correction-count` flags; atomic write to `state/catch_up_runs.yaml` |
| `scripts/skill_runner.py` | +40 lines | Move cache to `state/`, import `lib/skill_health`, call health tracking after each skill, R7 cadence reduction in `should_run()` |
| `scripts/briefing_adapter.py` | +25 lines | R7 footer display (~10 LOC), R8 meta-regression alarm (~15 LOC), `_load_catch_up_runs()` reads `catch_up_runs.yaml` (~5 LOC) |
| `scripts/eval_runner.py` | +30 lines | `--skills` flag (skill health table) |
| `config/workflow/finalize.md` | +10 lines | Step 16 signal-noise CLI flags; skill health display instruction |
| `config/workflow/process.md` | +5 lines | Note new cache location; `source: system` on Step 7b OIs |
| `config/commands.md` | +5 lines | Document `/eval effectiveness` and `/eval skills` commands |

### Test Files
| File | Tests | Purpose |
|------|-------|---------|
| `tests/unit/test_skill_health.py` | ~25 | `is_zero_value()` with skill-specific overrides, `is_stable_value()`, health classification, counter updates, recovery rules |
| `tests/unit/test_r2_activation.py` | ~10 | R2 fires/doesn't fire with populated signal_noise in `catch_up_runs.yaml` |
| `tests/unit/test_r7_skill_cadence.py` | ~12 | R7 cadence reduction in `should_run()` at 10/20 thresholds, P0 exemption, recovery restoration |
| `tests/unit/test_r8_meta_alarm.py` | ~6 | R8 fires when engagement < 15% for 7/10, respects 14-day cooldown |
| `tests/unit/test_engagement_rate.py` | ~8 | health_check_writer correctly stores engagement fields in `catch_up_runs.yaml`, OI source tracking |

---

## 8. Migration Plan

### Backward Compatibility

- `skills_cache.json` move: `skill_runner.py` checks old path (`tmp/`) first,
  migrates to new path (`state/`) on first run. Existing cache data preserved.
  Health sub-dict is additive — old schema entries work without it.
- `catch_up_runs.yaml`: new file, no migration needed. Existing freeform
  Markdown run history in `health-check.md` body is preserved as-is (not
  deleted, not migrated). `_load_catch_up_runs()` reads the new YAML file;
  if it doesn't exist yet, returns empty list (same as current behavior).
- `signal_noise` in run entries is additive — R2 already handles
  missing field (returns None → no action).
- `skill_runner.py` health changes are append-only — wrapped in try/except,
  non-blocking. Skill execution is never affected by health tracking failures.
- R7 is additive — extends `should_run()` in `skill_runner.py`.
  When no health data exists, original cadence applies (no change).
- R8 is additive — new rule in `BriefingAdapter.recommend()`, returns None
  when insufficient data. Existing rules unaffected.
- OI schema extension (`source: system | user`) is additive — existing OIs
  without the field are treated as `source: system` by default.

### Bootstrap Sequence

1. **Phase 1 deploy:** First `skill_runner.py` run migrates cache to `state/`,
   starts health counters from zero. Health classifications become useful
   after ~10 runs — `uscis_status` shows degraded, `mental_health_utilization`
   shows broken. R7 cadence reduction activates for P1/P2 skills with 10+
   consecutive zeros.
2. **Phase 2 deploy:** First catch-up writes `signal_noise` field to
   `state/catch_up_runs.yaml` via `health_check_writer.py` new flags.
   `_load_catch_up_runs()` returns structured entries.
3. **After 10 catch-ups (~8 days at 1.3/day):** R2 has enough `signal_noise`
   data to evaluate.
4. **After 4 weeks:** Full effectiveness data has meaningful trend data.
5. **After 30 catch-ups:** Per-domain engagement (Phase 3+) has enough data
   for surgical R2 compression.

### Rollback

- Move `state/skills_cache.json` back to `tmp/skills_cache.json` — skill
  execution is unaffected (health sub-dict is ignored by existing code)
- Delete `state/catch_up_runs.yaml` — `_load_catch_up_runs()` returns
  empty list, R2 returns to dormant state
- Revert `health_check_writer.py` CLI flags — harmless (flags ignored)
- Revert `briefing_adapter.py` R7/R8 — no behavioral change without data
- Revert `eval_runner.py` added functions — eval commands return to v1 surface
- Remove `scripts/lib/skill_health.py` — `skill_runner.py` reverts to
  original code without health tracking
- Total revert: ~160 lines across 5 files + 1 new lib. No data loss.

---

## 9. Resolved Questions

These were open in v1.0; positions taken based on architectural review:

| # | Question | Resolution | Rationale |
|---|----------|-----------|-----------|
| 1 | Include Step 19 corrections in engagement rate? | **Yes.** Tracked as separate `correction_count` field, included in numerator. | Corrections are the purest user engagement signal — the human is actively closing the loop. |
| 2 | Auto-disable broken skills? | **No auto-disable. Auto-reduce cadence (R7).** | P6 (earned autonomy): reducing frequency is low-risk and reversible; disabling a P0 skill is not. R7 prompts at 20 zeros — user decides. |
| 3 | Content hash vs mtime for state file change detection? | **Content hash (SHA-256).** | 50ms for 55 files is negligible in a multi-minute catch-up. Correctness > simplicity when cost is near-zero. |
| 4 | Per-domain vs aggregate action rate? | **Aggregate first, per-domain in Phase 3.** | Per-domain attribution requires reliable domain-level data (≥30 catch-ups). Aggregate engagement rate is meaningful from day 1 and sufficient for R2 activation. Per-domain enables surgical R2 compression once data is trustworthy. |
| 5 | Separate `skill_health.yaml` vs unified cache? | **Unified cache.** Promote `skills_cache.json` to `state/`, add health counters inline. | One file, one schema, one lifecycle. No shadow caches. Consistent with Artha's "state lives in one place" principle. |
| 6 | New `effectiveness_tracker.py` script? | **No.** Extend `health_check_writer.py` with CLI flags. | "Claude Code IS the application" (Tech Spec §1) — Claude computes engagement during normal Steps 8–19; scripts handle deterministic writes only. |
| 7 | New `eval_view.py` / `health_check_view.py`? | **No new scripts.** `--skills` added to `eval_runner.py`. `/eval effectiveness` is Claude-rendered from `catch_up_runs.yaml` directly. | Scripts for computation; Claude for narrative. Engagement trends are small data — Claude reads and narrates better than a script formats. |
| 8 | `catch_up_runs` in health-check.md frontmatter or separate file? | **Separate `state/catch_up_runs.yaml` file.** | YAML arrays in frontmatter grow unbounded and corrupt via regex upsert. Separate file = simple append-only YAML, no frontmatter surgery, existing `_update_frontmatter()` untouched. One file per concern. |
| 9 | Bootstrap health counters from existing data? | **No. Start from zero.** | `skills_metrics.json` has 2 entries. Bootstrapping adds 15 LOC of one-time migration for ~2 data points. After 10 real catch-ups (8 days), bootstrapped data is <20% of total. Accept the warm-up. |
| 10 | R7 in `briefing_adapter.py` or `skill_runner.py`? | **Cadence logic in `skill_runner.py`; display in adapter.** | The adapter adjusts briefing format. Cadence is about whether a skill *executes* — that belongs in the executor (`should_run()`). Clean separation: execution logic in executor, display logic in adapter. |
| 11 | `_is_zero_value()` generic or skill-aware? | **Skill-aware.** Accepts `skill_name` + `skills_config`, checks per-skill `zero_value_fields` override first, falls back to generic detection. | `king_county_tax` returns data but only `tax_amount` matters. Per-skill overrides in `skills.yaml` enable tuning without code changes. |
| 12 | What happens when a degraded skill recovers? | **Self-healing recovery rules.** Degraded → healthy on first non-zero run. Broken → healthy after 3 consecutive successes. R7-reduced cadence restored on recovery. | Without recovery, a skill degraded at catch-up 10 stays degraded at catch-up 100 even if returning good data since catch-up 30. |

---

## Appendix A: Skill Health Classification Matrix

| Condition | Health | Example |
|-----------|--------|---------|
| total_runs < 5 | ⏳ warming_up | newly added skill (no adaptation fires) |
| success_rate ≥ 80%, had nonzero value in last 10 runs | ✅ healthy | bill_due_tracker, credit_monitor |
| success_rate ≥ 80%, consecutive_zero ≥ 10 | ⚠️ degraded | uscis_status (succeeds but empty) |
| success_rate ≥ 80%, consecutive_stable ≥ 10 | 🔄 stable | king_county_tax (valid data, never changes) |
| success_rate < 50% over last 10 runs | 🔴 broken | mental_health_utilization (always fails) |
| enabled: false in skills.yaml | ⏸ disabled | property_tax (manually disabled) |

## Appendix B: Current Skill Inventory (as of 2026-03-26)

| Skill | Cadence | Priority | Status Today | Expected Health |
|-------|---------|----------|-------------|-----------------|
| uscis_status | every_run | P0 | Returns `{}` | degraded |
| passport_expiry | every_run | P1 | Returns valid data | healthy |
| subscription_monitor | every_run | P1 | Returns valid data | healthy |
| financial_resilience | weekly | P1 | Returns "insufficient_data" | degraded |
| relationship_pulse | every_run | P1 | changed=False (stable) | healthy |
| occasion_tracker | every_run | P1 | changed=False (stable) | healthy |
| bill_due_tracker | every_run | P1 | Returns upcoming bills | healthy |
| whatsapp_last_contact | every_run | P1 | changed=False | healthy |
| credit_monitor | every_run | P1 | 6 alerts (some false positives) | healthy |
| school_calendar | daily | P1 | changed=True | healthy |
| home_device_monitor | daily | P1 | 5 offline devices | healthy |
| mental_health_utilization | daily | P1 | status=failed | broken |
| ai_trend_radar | weekly | P2 | 0 signals (empty feeds) | degraded |
| work_background_refresh | daily | P1 | status=failed | broken |
| nhtsa_recalls | weekly | P1 | Returns valid data | healthy |
| king_county_tax | daily | P1 | Returns data (unchanged) | stable |
| noaa_weather | every_run | P1 | Returns valid data | healthy |
| visa_bulletin | weekly | P0 | Returns valid data | healthy |

## Appendix C: R2 Activation Timeline

```
Day 0:  Deploy Phase 1 (skill health) + Phase 2 (catch_up_runs.yaml + signal_noise)
Day 1:  First catch-up writes signal_noise to state/catch_up_runs.yaml  ← data starts
Day 2:  2 runs with signal_noise
...
Day 8:  10 runs with signal_noise                           ← R2 can evaluate
Day 8+: R2 checks if ≥7 of last 10 runs have signal_noise < 0.30
        If yes → aggregate domain_item_cap=3
        If no  → R2 stays dormant (engagement rate is healthy)
Day 23: ~30 runs with signal_noise                          ← Phase 3: per-domain R2
```

The 10-run cold-start gate prevents premature compression.
Per-domain R2 is Phase 3 — requires per-domain engagement data first.

## Appendix D: R7 Cadence Reduction Timeline

```
Run 1–9:   Skill runs at configured cadence. Zeros accumulate silently.
Run 10:    consecutive_zero == 10 → R7 auto-reduces cadence (P1/P2 only)
           every_run → daily, daily → weekly
           Briefing footer: "[Skill description] now checks less often (no new data recently)"
Run 11–19: Runs at reduced cadence. Counter continues.
Run 20:    consecutive_zero == 20 → one-time prompt in briefing:
           "[Skill description] has been quiet for a while — disable it? [yes / keep]"
           User "yes" → enabled: false in skills.yaml
           User "keep" → suppress_zero_prompt: true (no further prompts)
P0 skills: Never auto-reduced. Only prompted at 20 zeros.
Recovery:  If a cadence-reduced skill returns non-zero data on any run,
           restore original cadence from config/skills.yaml immediately.
           Log: "[Skill description] now checks at normal frequency again"
```

## Appendix D.1: Recovery Rules

```
Classification transitions (automatic, no user approval needed):

degraded → healthy:  Current run is non-zero-value.
                     Reset consecutive_zero = 0. Reclassify immediately.

broken → healthy:    Last 3 consecutive runs all succeeded.
                     Reclassify immediately.

R7-reduced → normal: Cadence-reduced skill returns non-zero data.
                     Restore original cadence from skills.yaml.

These transitions are self-healing — the system never gets stuck in a
degraded state when the underlying problem resolves.
```

## Appendix D.2: R8 Meta-Regression Timeline

```
Run 1–6:  Engagement rate accumulates. R8 dormant.
Run 7–10: If ≥7 of last 10 runs have engagement < 0.15:
          → R8 fires one-time prompt:
            "⚠️ Low engagement — adjust scope / disable domains / flash-only / keep?"
          User response logged. "Keep" suppresses R8 for 30 days.
14-day cooldown: R8 fires at most once per 14-day window.
Purpose: The effectiveness loop monitors itself — prevents silent regression.
```

## Appendix E: Architecture Alignment Summary

| Artha Principle | How this spec adheres |
|----------------|----------------------|
| "Claude Code IS the application" (Tech Spec §1) | No new reasoning scripts. Claude computes engagement; scripts write deterministically. `/eval effectiveness` is Claude-rendered — scripts for computation, Claude for narrative. |
| "Zero custom code is the target" (Tech Spec §4) | One new shared lib (~50 LOC). ~110 LOC total modifications to 5 existing scripts. |
| "State lives in Markdown/YAML" (Tech Spec §3) | Unified cache in `state/`. Structured YAML in `catch_up_runs.yaml`. No shadow state. |
| P6 Earned Autonomy (PRD) | R7 auto-reduces cadence (safe, reversible). Never auto-disables. Human approves disabling. R8 meta-alarm surfaces self-monitoring. |
| UX-1 Silence is default (UX Spec) | Skill health invisible in briefing unless broken/degraded. Effectiveness dashboard is pull-only (`/eval`). |
| UX-3 Progressive disclosure (UX Spec) | Briefing shows broken count → `/eval skills` shows full table → `state/skills_cache.json` has raw data. |
| Self-healing (new) | Recovery rules promote degraded/broken skills back to healthy when data recovers. R7 cadence restores on recovery. System never gets stuck. |
