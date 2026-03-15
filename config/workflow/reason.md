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

### Step 8 — Cross-domain reasoning

**This is the highest-value step — it produces intelligence no single-domain analysis can provide.**

Required pairings to check (MINIMUM):
- Immigration deadline ≤90 days + upcoming travel → flag travel/advance-parole conflict
- Bill due date + low cash balance → flag timing risk
- Kids school event + work calendar conflict → surface to user
- Open item overdue + no progress signal → escalate priority

**8a — URGENCY × IMPACT × AGENCY scoring:**
For each item and cross-domain insight:
```
URGENCY: 3=deadline ≤7d | 2=deadline 8-30d | 1=deadline 31-90d | 0=none
IMPACT:  3=affects immigration/finance/health of 2+ people | 2=single, significant | 1=minor
AGENCY:  3=clear action today | 2=action needs info first | 1=monitoring | 0=none
Score = URGENCY × IMPACT × AGENCY
ONE THING = highest scoring item
```
Display: `[U×I×A = N] [domain]`

**8b — Net-negative write guard** (applies to all Step 7 writes — already enforced there)

**8c — Post-write verification** (applies to all Step 7 writes — already enforced there)

**8g — Consequence forecasting (🔴 Critical + 🟠 Urgent items only):**
For items rated Critical/Urgent, generate IF YOU DON'T chain:
```
IF YOU DON'T: [action]
TIMELINE:     [when consequences begin]
FIRST ORDER:  [immediate consequence]
CASCADE:      [what follows]
```
Max 3 forecasts per briefing (1 in flash mode).

**8h — Dashboard rebuild (skip in read-only mode):**
Rebuild `state/dashboard.md` with current data from all domains.

**8j — Fastest Next Action (FNA):**
For 🔴/🟠 items: `fna_score = urgency_impact_agency / (friction × time_estimate)`
Attach to each: `→ Fastest action: [action] ([time], [friction])`

**8n — PII Guard stats:**
Retrieve from pii_guard.py output for briefing footer:
`{emails_scanned: N, redactions_applied: N}`

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
