---
phase: reason
steps: 8–11
source: config/Artha.core.md §2, Steps 8–11
---

## ⛩️ PHASE GATE — Reason

**If running /catch-up and you haven't loaded this file yet, STOP and read it now.**

**Before executing this phase, verify:**
- [ ] Process phase complete (all domains processed per process.md)
- [ ] Domain extraction results are available (briefing contributions from each domain)
- [ ] Open items updated (Step 7b)

**Cross-domain connections REQUIRED (minimum — do not skip any):**
- [ ] Immigration + Finance
- [ ] Kids school + Calendar
- [ ] Health + Calendar
- [ ] Travel + Immigration

**If ANY prerequisite is not met, STOP and complete it first.**

---

## Steps

### Step 8 — Cross-Domain Reasoning (OODA Protocol)

**This is the highest-value step — it produces intelligence no single-domain analysis can provide.**
**Execute the full 4-phase OODA protocol below. Do NOT skip any phase.**

---

### 8-O: OBSERVE — Gather Evidence

Collect ALL signals from the completed domain processing:
- List every alert triggered (with severity and domain)
- List every state file mutation (what changed, delta magnitude)
- List every anomaly detected (unusual patterns, thresholds crossed)
- Note which domains had NO activity (absence is signal)

**Read `state/memory.md` facts section.** For each fact present:
- **Corrections:** Suppress any alert that matches a correction fact (type: correction, confidence ≥ 0.9)
- **Patterns:** Reference known patterns when evaluating anomalies (type: pattern)
- **Thresholds:** Use user-calibrated thresholds instead of system defaults (type: threshold)

**Output: `[OBSERVE]` block** with structured signal inventory and applied corrections from memory.

---

### 8-Or: ORIENT — Analyze Cross-Domain Connections

For EACH mandatory cross-domain pair below, explicitly state whether a connection exists
and what it implies:

| Pair | Why Check |
|------|-----------|
| Immigration × Finance | Visa fees, status changes affect employment authorization |
| Immigration × Calendar | Filing deadlines, interview dates, document expiry |
| Kids/School × Calendar | School events, parent-teacher, activity conflicts |
| Health × Calendar | Appointments, prescription refills, lab follow-ups |
| Travel × Immigration | Visa validity for travel, re-entry requirements, advance parole |
| Finance × Home | Property tax, utility anomalies, maintenance costs |
| Employment × Finance | Payroll changes, benefits enrollment, RSU vesting |
| Goals × [All Domains] | Which goals made progress? Which are stalled? |

For each pair: "Connection found: [description]" or "No connection."

Additionally, check for **COMPOUND SIGNALS** — situations where 2+ domains together imply
something that neither domain alone would surface:
- Example: Immigration filing receipt + Finance large wire = filing fee paid
- Example: Health appointment + Calendar conflict = needs rescheduling
- Example: Kids school break + Travel booking = family trip context

**Output: `[ORIENT]` block** with connection matrix and compound signals list.

---

### 8-D: DECIDE — Prioritize with U×I×A Scoring

From the ORIENT output, score ALL findings using URGENCY × IMPACT × AGENCY:

```
URGENCY: 3=deadline ≤7d | 2=deadline 8-30d | 1=deadline 31-90d | 0=none
IMPACT:  3=affects immigration/finance/health of 2+ people | 2=single, significant | 1=minor
AGENCY:  3=clear action today | 2=action needs info first | 1=monitoring | 0=none
Score = URGENCY × IMPACT × AGENCY
```

Display each scored item: `[U×I×A = N] [domain] — [description]`

Rank ALL findings by score:
1. 🔴 Critical (score ≥ 6): action TODAY
2. 🟠 Urgent (score 3–5): action THIS WEEK
3. 🟡 Standard (score 1–2): informational, notable
4. 🔵 Info (score 0): low-priority

Select the **ONE THING** — the single highest-scoring item that is:
- **Actionable** (user can do something about it)
- **Time-sensitive** (matters NOW, not next month)
- **High-impact** (affects a high-stakes domain or multiple domains)

**Output: `[DECIDE]` block** with prioritized action list, scored items, and ONE THING selection.

---

### 8-A: ACT — Validate, Enrich, and Output

**8-A-1 — Validation:** Before finalizing, verify:
- Does each alert cite a specific data source (email, calendar event, state file)?
- Are there any claims not grounded in observed data? ← REMOVE THESE
- Does the ONE THING pass the "so what?" test?
- Any domain that had activity but produced zero alerts — re-examine.

If validation fails for an item: cycle back to ORIENT for that item.
Maximum cycles: 2. After 2 cycles, proceed with best-effort output.

**8-A-2 — Consequence forecasting (🔴 Critical + 🟠 Urgent items only):**
For items rated Critical/Urgent, generate IF YOU DON'T chain:
```
IF YOU DON'T: [action]
TIMELINE:     [when consequences begin]
FIRST ORDER:  [immediate consequence]
CASCADE:      [what follows]
```
Max 3 forecasts per briefing (1 in flash mode).

**8-A-3 — Fastest Next Action (FNA):**
For 🔴/🟠 items: `fna_score = urgency_impact_agency / (friction × time_estimate)`
Attach to each: `→ Fastest action: [action] ([time], [friction])`

**8-A-4 — Dashboard rebuild (skip in read-only mode):**
Rebuild `state/dashboard.md` with current data from all domains.

**8-A-5 — PII Guard stats:**
Retrieve from pii_guard.py output for briefing footer:
`{emails_scanned: N, redactions_applied: N}`

**Output: `[ACT]` block** confirms validation passed. Validated findings flow into Step 9 (briefing synthesis).

**Checkpoint (Step 8 complete):** After OODA Act phase, write:
```bash
python -c "from scripts.checkpoint import write_checkpoint; from pathlib import Path; write_checkpoint(Path('.'), 8, ooda_completed=True)"
```

### Step 9 — Web research (if triggered)

Triggered by time-sensitive external data only:
- Visa Bulletin changes (monthly) → use skill_runner.py data
- Regulatory updates affecting immigration path
- Weather advisories for travel plans

Use `python scripts/safe_cli.py gemini "<query>"` for external lookups (PII-filtered).
Never use live web browsing directly.

### Step 10 — Ensemble reasoning (high-stakes only)

For 🔴 Critical decisions (immigration deadlines, large financial moves):
- Present multiple risk scenarios
- Include confidence levels
- Flag "this needs a professional" when stakes exceed self-serve threshold

### Step 11 — Synthesize briefing

Assemble final briefing from all domain contributions.
Format per `config/briefing-formats.md` for the current `briefing_format`.

Priority ordering:
1. 🔴 Critical alerts (URGENCY×IMPACT×AGENCY ≥ 6)
2. 🟠 Urgent actions + FNA annotation
3. 🟡 Standard domain summaries
4. 🔵 Informational/awareness
5. ONE THING block
6. Periodic sections (Week Ahead if Monday, Weekend Planner if Friday)

In read-only mode: do NOT write to `briefings/YYYY-MM-DD.md`.
In normal mode: archive to `briefings/YYYY-MM-DD.md`.

## Error handling
- Cross-domain reasoning failures produce partial briefing (better than nothing)
- Research failures logged but don't block briefing
- Individual domain reasoning errors = log and continue

---
## ✅ Phase Complete — Transition
→ **Load `config/workflow/finalize.md` now.** Do NOT proceed without it.
