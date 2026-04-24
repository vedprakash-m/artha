---
phase: fetch
steps: 3–4e
source: config/Artha.core.md §2, Steps 3–4e
---

## S-30 Context Gate — Session Start Budget

**Do NOT load the following at session start (load only on explicit commands):**
- `state/work/reflect-history.md` → load only for `reflect` or `deep reflect`
- `state/work/evidence_lake/` files → load only for `connect`, `reflect`, or `deep reflect`

**Load only at session start:**
- `config/Artha.core.md`
- `config/Artha.md`
- Active workflow files for the current command
- `tmp/_session_recap.yaml` (if present and fresh — via S-03)

**Budget:** Core session-start files must total ≤ 35,000 tokens.

---

## ⛩️ PHASE GATE — Fetch

**If running /catch-up and you haven't loaded this file yet, STOP and read it now.**

**Before executing this phase, verify:**
- [ ] Preflight (Step 0) has completed (exit 0 or advisory)
- [ ] `briefing_format` and `hours_elapsed` are set (from preflight.md Steps 2–2b)
- [ ] Read-only mode status is known

**MANDATORY: You MUST read at least these state files before proceeding to Step 5:**
- `state/health-check.md` — always
- `state/open_items.md` — always
- `state/memory.md` — always
- `state/comms.md` — always (Tier A)
- `state/calendar.md` — always (Tier A)
- `state/goals.md` — always (Tier A)

**Reading ZERO state files is a WORKFLOW VIOLATION. Briefings without state context are invalid.**

**If ANY prerequisite is not met, STOP and complete it first.**

---

## Steps

### Step 3 — Periodic triggers

Set session flags based on today's date and `state/health-check.md` data:

- **Monday**: `week_ahead = true` (add §8.11 Week Ahead section in Step 11)
- **Monday** AND `work.thrivesync.enabled`: `thrivesync_due = true` (add §8.14 ThriveSync nudge in Step 11)
- **Friday**: `weekend_planner = true` (add §8.12 Weekend Planner in Step 11)
- **1st of month** AND last monthly retro >28 days ago: `generate_monthly_retro = true`
- **Monday** AND last weekly summary >8 days ago: `generate_weekly_summary = true`
- **Goal sprint at 14-day mark**: `sprint_calibration = true` for that sprint
- **Decision deadline ≤14 days**: `decision_deadline_warning = true`

Run skills:
```bash
python scripts/skill_runner.py
```
Output: `tmp/skills_cache.json`. Ingested in Step 5 to supplement email data.

### Step 4 — Fetch (IN PARALLEL — all sources simultaneously)

**MCP tools (preferred):**
```
artha_fetch_data(since="$LAST_CATCH_UP", max_results=200)
artha_run_skills()   # run data fidelity skills in parallel
```

**Pipeline (when MCP unavailable):**
```bash
python scripts/pipeline.py --since "$LAST_CATCH_UP" --verbose
```
Fetches all enabled connectors simultaneously (ThreadPoolExecutor, max 8 workers).
In read-only mode: Outlook and iCloud connectors will fail (network blocked) — log as
`⚠️ Connector offline: [name] — network blocked in this environment (not an auth failure)`.

**MCP retry protocol (MANDATORY):**
For any MCP source returning zero results:
1. Retry with tighter date range (today only)
2. Retry with broader query (remove filters)
3. If still zero: accept, log "0 results — verified with 3 queries"

**Google Calendar:** ALWAYS query ALL calendar IDs from `user_profile.yaml`
→ `integrations.google_calendar.calendar_ids`. If not configured, query "primary" + log warning.
Silently dropping secondary calendars (family, US Holidays) is a workflow violation.

**Error handling for VM/network failures:**
- `graph.microsoft.com` blocked → note in footer: "Outlook unavailable — VM network constraint. Run from Mac for full data."
- `imap.mail.me.com` blocked → note in footer: "iCloud unavailable — VM network constraint."
- Do NOT suggest re-running auth setup for network-blocked connectors

**AR-8: Connector Failure Root-Cause Protocol**
If a connector returns an error (not zero results — actual error):
1. Classify: transient (network/timeout/rate-limit) vs. configuration vs. logic vs. environmental?
2. Transient → ONE automatic retry (pipeline's `lib/retry.py` handles this). If retry fails: log + continue.
3. Configuration → report the specific issue (e.g., "Gmail token expired"). Do NOT retry.
4. Environmental → note in footer (see VM/network above). Do NOT retry.
5. Log root-cause diagnosis to `state/audit.md`: `connector | error | diagnosis | action`.
6. Never blind-retry the same failing call more than once without changing the approach.

### Step 4b — Tiered Context Loading

After fetch, load domain state files by tier:

| Tier | Condition | Action |
|------|-----------|--------|
| `always` | Core files | Load: health-check.md, memory.md, open_items.md, comms.md, calendar.md |
| `active` | last_activity ≤30 days | Load fully |
| `reference` | last_activity 30–180 days | Load frontmatter + last 30 lines |
| `archive` | last_activity >180 days | Skip unless new emails route here |

Tier A domains (always load regardless of routing): `calendar`, `comms`, `goals`, `finance`, `immigration`, `health`

### Step 4c — Bootstrap State Detection

For each loaded state file with `updated_by: bootstrap`:
- Flag domain as bootstrap
- Prepend to briefing header: "⚠ UNPOPULATED STATE FILES: [list] — best-effort extraction only"

### Step 4d — Email Volume Tier Detection

```
≤50 emails:   volume_tier = "standard"   (1,500-token cap per email)
51–200:        volume_tier = "medium"    (aggressive suppression, 1,000-token cap)
201–500:       volume_tier = "high"      (two-pass: P0 domains first)
>500:          volume_tier = "extreme"   (three-pass: P0 full, P1 summary, P2 count-only)
```

### Step 4e — Offline / Degraded Mode Detection

```
if working_connectors == 0:     mode = "offline"   → state-only briefing
elif failed_connectors > 0:     mode = "degraded"  → partial briefing with data gap notes
else:                            mode = "normal"
```

Log mode to `health-check.md → session_mode`.

**Checkpoint (Step 4 complete):** After fetch + mode detection, write:
```bash
python -c "from scripts.checkpoint import write_checkpoint; from pathlib import Path; write_checkpoint(Path('.'), 4, email_count=N, session_mode='normal|degraded|offline')"
```
Replace `N` with the actual email count and `session_mode` with the detected mode.

## Error handling
- Individual connector failures don't block other connectors
- Pipeline exit code 3 = partial success
- All connector failures = offline mode, state-only briefing

---
## ✅ Phase Complete — Transition
→ **Load `config/workflow/process.md` now.** Do NOT proceed without it.
