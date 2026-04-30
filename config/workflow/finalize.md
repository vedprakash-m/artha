---
phase: finalize
steps: 12–19b
source: config/Artha.core.md §2, Steps 12–19b
---

## ⛩️ PHASE GATE — Finalize

**If running /catch-up and you haven't loaded this file yet, STOP and read it now.**

**Before executing this phase, verify:**
- [ ] Reason phase complete (briefing synthesized per Step 11)
- [ ] All domain briefing contributions are assembled
- [ ] ONE THING, FNA, and cross-domain insights are identified

### Read-Only Mode Exceptions

If read-only mode is active, **SKIP** these write steps:
- ⏭️ Step 7, 7b (already skipped in process.md)
- ⏭️ Step 14 — email briefing
- ⏭️ Step 15 — Microsoft To Do push
- ⏭️ Step 16 — health-check update
- ⏭️ Step 17 — audit log
- ⏭️ Step 18 — vault re-encryption + tmp cleanup
- ⏭️ Step 19 — accuracy calibration (requires state write)

Log each: `⏭️ Step N skipped — read-only mode`

**If ANY prerequisite is not met, STOP and complete it first.**

---

## Steps

### Step 2b — Collect Retrospective Outcome Signals *(Eval Layer)*

**SKIP if** `harness.eval.outcome_signals.enabled: false` in `config/artha_config.yaml`
**SKIP in read-only mode** → log `⏭️ Step 2b skipped — read-only mode`
**SKIP if** no previous run found in `state/catch_up_runs.yaml`

Before beginning finalization, collect retrospective outcome signals from the
previous session and backfill them into the previous run record:

```python
from pathlib import Path
from scripts.artha_context import _get_last_run, collect_outcome_signals, _backfill_run_record

artha_dir = Path(".")
prev_run = _get_last_run(artha_dir)
if prev_run and not prev_run.get("outcome_corrections_next_session"):
    outcomes = collect_outcome_signals(prev_run, {}, artha_dir)
    if outcomes:
        _backfill_run_record(artha_dir, prev_run.get("session_id", ""), outcomes)
        print(f"[Step 2b] Outcome signals backfilled to session {prev_run.get('session_id','?')[:8]}…")
```

Outcome fields populated: `outcome_corrections_next_session`, `outcome_items_resolved_24h`,
`outcome_user_queries_since`, `outcome_briefing_referenced`, `outcome_user_absence_flag`.

**Failure is non-blocking** — finalize continues regardless of errors.

---

### Step 11c — Persistent Fact Extraction

**SKIP in read-only mode** → log `⏭️ Step 11c skipped — read-only mode`

After the briefing is finalized (Step 20), run the deterministic memory pipeline.
Step 20 provides the exact briefing path it wrote:

```bash
python3 scripts/post_catchup_memory.py --briefing briefings/YYYY-MM-DD.md
```

Replace `YYYY-MM-DD.md` with the actual filename produced by Step 20. The
`--briefing` flag is **required** — do not use `--discover` here (that is for
bootstrap and manual re-processing only).

The script automatically:
1. Creates a session summary in `tmp/session_history_N.md` (for search/recovery)
2. Extracts durable facts **directly from the briefing** (bypasses the lossy
   SessionSummary round-trip — uses the full domain content)
3. Persists new facts to `state/memory.md` (AR-1 capacity enforced,
   domain sensitivity tiering applied)
4. Updates `state/self_model.md` if ≥5 catch-up runs on record (AR-2)
5. Appends a structured run record to `state/memory_pipeline_runs.jsonl`
6. Prints: `[run_id] N facts persisted (M extracted), self-model [updated|unchanged]`

**Failure is non-blocking** — catch-up completes regardless of errors.

Config kill switch: set `harness.agentic.post_catchup_memory.enabled: false`
in `config/artha_config.yaml` to disable the entire pipeline.

If for any reason the script cannot be called (AI CLI context, read-only mode,
or harness unavailable), fall back to the manual invocation:

```python
from pathlib import Path
from scripts.fact_extractor import extract_facts_from_summary, persist_facts
import glob

summaries = sorted(glob.glob('tmp/session_history_*.md'))
if summaries:
    facts = extract_facts_from_summary(Path(summaries[-1]), Path('.'))
    count = persist_facts(facts, Path('.'))
    print(f'Extracted {count} new facts to state/memory.md')
else:
    print('No session summary found — skipping fact extraction')
```

After pipeline run, write calendar events to state/calendar.md:
```bash
python3 scripts/calendar_writer.py --input tmp/pipeline_output.jsonl
```
If `tmp/pipeline_output.jsonl` was not written (AI CLI mode), skip silently.

If during this session the user corrected a previous finding (e.g., "that's not an anomaly"
or "ignore X"), an explicit correction fact is created automatically. These corrections
will suppress matching alerts in future sessions via the OODA OBSERVE phase.

#### Memory Capacity Check (AR-1)

After extracting facts, `persist_facts()` automatically enforces capacity limits
(≤ 30 facts, ≤ 3,000 characters). If limits are exceeded, consolidation fires automatically:
1. TTL-expired facts are removed first.
2. Lowest-confidence, non-protected facts are evicted until within limits.
3. User corrections (type: correction) and preferences are **never** auto-evicted.

The limit is the discipline — it forces distillation of the most valuable knowledge.

#### Self-Model Update (AR-2)

After fact extraction, update `state/self_model.md` **only if** genuine insight was gained
about your own performance this session:
- A domain where you were notably accurate or inaccurate
- A user preference about communication style discovered
- An effective strategy worth preserving
- A mistake worth noting as a blind spot

Keep total content ≤ 1,500 characters. Consolidate if approaching limit.
**Do NOT update every session** — only when real insight emerged.

#### Procedure Extraction (AR-5)

If during this session:
- You completed a task requiring 5+ distinct tool calls or file operations
- You encountered errors and found the working path through trial
- The user corrected your approach and the correction led to success

Then extract the working procedure:
1. Search `state/learned_procedures/` — does a similar procedure exist?
   - If yes, patch the existing file (update steps, add new pitfalls discovered)
   - If no, create: `state/learned_procedures/{domain}-{slug}.md`
2. Format: trigger, steps, pitfalls, verification (≤ 1,500 chars)
3. **Threshold**: only create when genuinely non-obvious. Simple tasks don't qualify.

### Step 11d — Work State Refresh (post-memory-pipeline)

**SKIP when** `work.enabled` is `false` in `config/user_profile.yaml`.
**SKIP when** `work.refresh.run_on_catchup` is `false` in `config/user_profile.yaml`.
**SKIP in read-only mode** → log `⏭️ Step 11d skipped — read-only mode`

After Step 11c completes (or is skipped), trigger a work state re-evaluation:

```bash
python scripts/post_work_refresh.py --quiet
```

The script re-runs the Work OS read loop to refresh learned-state metrics
(career_velocity, meeting_quality, days_since_bootstrap) without invoking any
live connectors.  Output appended to `state/work/eval/work-refresh-log.jsonl`.

**Failure is non-blocking** — catch-up completes regardless of errors.

Config kill-switch: set `work.refresh.run_on_catchup: false` in
`config/user_profile.yaml` to disable.

---

### Step 12 — Surface active alerts

Scan all processed domains for P0/P1 alerts.
Format with severity indicators per their URGENCY×IMPACT×AGENCY scores.

**Skill output integration:** If `skill_runner.py` produced output this session,
incorporate it into the relevant briefing sections:
- `relationship_pulse` → RELATIONSHIP PULSE section (stale contacts, overdue reconnects)
- `occasion_tracker` → OCCASIONS & WISHES section (🔴 within 3 days, 🟠 within 7, 🟡 within 14)
- `bill_due_tracker` → Finance section (bills due soon or overdue)
- `credit_monitor` → Finance section (🔴 fraud alerts surface before all other Finance items)
- `school_calendar` → Kids section (upcoming school events, grade alerts)

Skill output for `occasion_tracker` with imminent items (≤ 3 days) should be
elevated to 🔴 CRITICAL if the person is in `core_family`, or 🟠 URGENT for
`extended_family_india` and `best_friends` circles.

### Step 12.5 — Compose action proposals from domain signals *(Action Layer)*

**SKIP if** `actions.enabled: false` in `config/artha_config.yaml` → log `⏭️ Step 12.5 skipped — actions disabled`
**SKIP in read-only mode** → log `⏭️ Step 12.5 skipped — read-only mode`

```bash
python3 scripts/action_orchestrator.py --run
```

The orchestrator reads:
- `tmp/pipeline_output.jsonl` (from Step 4)
- `tmp/ai_signals.jsonl` (from Steps 7–8, if AI wrote any)
- `state/*.md` (pattern engine evaluates state files)

Output: numbered pending action list on stdout for the AI to embed in the
briefing. Errors to stderr. Exit code: 0=ok, 1=partial, 3=failure.

### Step 12b — Digest connector logs *(Eval Layer)*

**SKIP if** `harness.eval.log_digest.enabled: false` in `config/artha_config.yaml`
**SKIP in read-only mode** → log `⏭️ Step 12b skipped — read-only mode`

After action proposals are queued, digest the structured JSONL connector logs:

```bash
python scripts/log_digest.py
```

Writes `tmp/log_digest.json` with per-connector error rates, p95 latencies,
error budget percentage (target: <5%), and any detected anomalies
(HIGH_ERROR_RATE, TREND_WORSENING, QUIET_FAILURE).

Anomalies are written to `state/eval_alerts.yaml` by the post-catch-up memory
pipeline (Step 11c runs `_run_log_digest()` internally). This step is for
explicit standalone invocation when the memory pipeline is disabled or
read-only mode is active.

**Failure is non-blocking** — catch-up completes regardless.

---

### Step 12c — Briefing Confidence Footer *(Eval Layer)*

**SKIP if** Step 12b produced null `quality_score` (eval scorer failed or disabled)
**SKIP in read-only mode** → log `⏭️ Step 12c skipped — read-only mode`

After scoring completes, inject a one-line confidence footer at the end of the
briefing file:

```
If Step 12b produced a quality_score and compliance_score:
    Append to the briefing file:
    ---
    📊 Briefing confidence: {quality_score}/100 (Δ{delta} from last) | {domains_found}/{domains_expected} domains | 14d avg: {avg_14d} | {flagged_count} items need verification

Where:
    quality_score = from eval_scorer output
    delta = quality_score minus previous session's quality_score
            (read from state/briefing_scores.json last entry).
            Format: "+5" or "−3". If no previous entry: omit delta clause.
    avg_14d = rolling 14-day average quality_score from briefing_scores.json.
              If fewer than 3 data points: omit clause.
    domains_found / domains_expected = from completeness dimension
    flagged_count = count of items marked with ⚡ verification overlay (from Step 6b)

If Step 12b failed (null scores): do NOT append a footer. Absence of
footer = no eval data (user can infer this from the missing line).
```

**Non-blocking:** Footer append is wrapped in try/catch. If it fails, the
catch-up continues. The briefing file is left without a footer.

---

### Step 13 — Propose write actions

For each recommended action from domain processing:
- Present using Action Proposal Format (§9 in Artha.md)
- All write actions require explicit human approval before execution
- Actions: send_email, add_calendar_event, todo_sync, update_state

### Step 13.5 — Stage briefing for archival

**SKIP in read-only mode** → log `⏭️ Step 13.5 skipped — read-only mode`

Write the complete briefing text to `tmp/briefing_incoming_<runtime>.md`
where `<runtime>` is your surface identifier:

| Surface | `<runtime>` value |
|---|---|
| VS Code / Copilot | `vscode` |
| Gemini CLI | `gemini` |
| Claude Code | `claude` |
| Copilot CLI | `copilot` |

Output `💾 Briefing staged.` after the write succeeds.
The pipeline will ingest this file on the next run via `_ingest_pending_briefs()`.

**The `brief` command is only complete when `💾 Briefing staged.` is output.**

### Step 14 — Email briefing

**SKIP in read-only mode** → log `⏭️ Step 14 skipped — read-only mode`

If `briefing.email_enabled: true`:
```bash
python scripts/gmail_send.py --briefing
```
If `briefing.spouse_filtered: true`: generate filtered copy first.

### Step 14.5 — Push pending action queue to Telegram *(Action Layer)*

**SKIP if** `actions.enabled: false` → log `⏭️ Step 14.5 skipped — actions disabled`  
**SKIP if** Telegram channel not configured → log `⏭️ Step 14.5 skipped — no Telegram channel`  
**SKIP in read-only mode** → log `⏭️ Step 14.5 skipped — read-only mode`

After assembling the briefing (Step 14), push any pending action items to the Telegram
channel with inline approval keyboards.

Each pending action item is sent as a separate Telegram message with:

```
⚡ ACTION PENDING
Type: email_send | Domain: finance
Proposed: Send payment confirmation to landlord

[✅ Approve]  [❌ Reject]  [⏸ Defer]
```

Inline keyboard button data format:
- Approve → `act:APPROVE:{action_id}`
- Reject  → `act:REJECT:{action_id}`
- Defer   → `act:DEFER:{action_id}`

The `channel_listener.py` intercepts these callbacks and calls
`executor.approve()` / `executor.reject()` / `executor.defer()`.

**Rules:**
- Only send `pending` and `pre_approved` actions (not already-approved or expired)
- Cap at 10 items per session to avoid Telegram spam
- High-friction items (`friction: high`) prepend `🔴 HIGH FRICTION —` to the message
- `autonomy_floor: true` items always shown, never auto-processed
- Previously-notified actions (in state/action_notified.json) are skipped

Update `state/action_notified.json` with the action IDs of sent notifications.

### Step 15 — Push new items to Microsoft To Do

**SKIP in read-only mode** → log `⏭️ Step 15 skipped — read-only mode`

If `integrations.microsoft_graph.todo_sync: true` AND token valid:
```bash
python scripts/todo_sync.py push
```
Sync `state/open_items.md` → Microsoft To Do lists.

### Step 16 — Update health-check

**SKIP in read-only mode** → log `⏭️ Step 16 skipped — read-only mode`

Call the script to atomically update `state/health-check.md` frontmatter, append to `state/catch_up_runs.yaml`, and rotate connector health logs older than 7 days to `tmp/connector_health_log.md`:

```bash
python3 scripts/health_check_writer.py \
    --last-catch-up "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --email-count N \
    --domains-processed domain1,domain2 \
    --mode normal \
    --briefing-format standard \
    --engagement-rate 0.33 \
    --user-ois 1 \
    --system-ois 3 \
    --items-surfaced 6 \
    --correction-count 0
```

Replace all values with the actuals from this session:
- `N`: actual email count from Step 4
- `domain1,domain2`: comma-separated list of domains processed
- `--mode`: one of `normal`, `degraded`, `offline`, `read-only`
- `--briefing-format`: one of `standard`, `flash`, `deep` (selected by `briefing_adapter.py` at Step 15)
- `--engagement-rate`: compute as `(user_ois + correction_count) / items_surfaced` (or omit if `items_surfaced == 0`; stored as `null` in that case)
- `--user-ois`: count of OIs added by user during or after the briefing (origin: user)
- `--system-ois`: count of OIs auto-extracted in Step 7b (origin: system)
- `--items-surfaced`: running counter from Steps 7–8 (P0/P1/P2 alerts generated)
- `--correction-count`: number of user corrections observed in Step 19 calibration

**R7 user-response forwarding:** If during the briefing the user responded to a R7 disable prompt:
- User says "yes" (disable skill): append `--disable-skill <skill_name>` to the `health_check_writer.py` call
- User says "keep" (suppress future prompts): append `--suppress-skill-prompt <skill_name>` to the call
- Both are logged to `state/audit.md` automatically by the script

**Skill Health display:** After Connector Health section in the briefing, if `state/skills_cache.json` exists and any skill has `classification: broken` or `classification: degraded`, run:
```bash
python3 scripts/eval_runner.py --skills
```
and show the Skill Health table inline. Skills with `classification: warming_up` (fewer than 5 runs) are excluded from the table unless `--verbose` is passed.

The script is idempotent — safe to call multiple times; each call increments `catch_up_count` by 1 and replaces bootstrap stubs with the proper schema.

In read-only mode: embed session metadata in briefing footer instead:
```
## Session Metadata
- environment: cowork_vm
- mode: read-only
- state_files_read: N (plaintext only)
- encrypted_domains_blind: [list]
```

### Step 17 — Log PII stats

**SKIP in read-only mode** → log `⏭️ Step 17 skipped — read-only mode`

Append PII filter stats to `state/audit.md`:
```
{date} | emails_scanned: N | redactions_applied: N | patterns_detected: N
```

### Step 18 — Ephemeral cleanup + Re-encrypt

**SKIP in read-only mode** → log `⏭️ Step 18 skipped — read-only mode`

```bash
# Clear ephemeral cache (including checkpoint and skills cache)
rm -f tmp/*.json tmp/*.jsonl tmp/.checkpoint.json

# Re-encrypt sensitive state
python scripts/vault.py encrypt
```
Verify `.artha-decrypted` lock file is removed.
If re-encryption fails: LOUD warning — `⛔ Re-encryption failed — sensitive state left decrypted`.

Also clear the checkpoint programmatically (belt-and-suspenders):
```bash
python -c "from scripts.checkpoint import clear_checkpoint; from pathlib import Path; clear_checkpoint(Path('.'))"
```

### Step 18b — Write Session Recap (S-03)
Write a session recap so the next session can orient quickly:
```python
from scripts.checkpoint import write_session_recap
from pathlib import Path
write_session_recap(
    Path('.'),
    worked_on=["<list what was worked on this session>"],
    status_changes=["<list any status changes observed>"],
    decisions=["<list key decisions or user preferences noted>"],
    next_actions=["<list deferred or pending items for next session>"],
)
```
Populate from the session's briefing output and any user corrections or requests.
Omit PII — use redacted summaries only. The file is written to `tmp/_session_recap.yaml`.



**SKIP in read-only mode** → log `⏭️ Step 19 skipped — read-only mode`

Compare today's extraction with yesterday's/last-catch-up state.
Flag contradictions or reversals (e.g., status: approved → pending).
Note uncertainty if extraction differs significantly.

**Correction tracking:** Each time the user corrects a finding ("that's wrong", "ignore X", "actually it's...") during or after the briefing, increment the `correction_count` by 1. Pass the total at Step 16 via `--correction-count`.

**User-requested OIs:** If the user asks to add an open item during or after the briefing ("add a reminder", "track this"), create it with `origin: user` in the OI schema and increment `--user-ois` for Step 16.

### Step 19.5 — Action layer summary *(Action Layer)*

**SKIP if** `actions.enabled: false` → log `⏭️ Step 19.5 skipped — actions disabled`

Append a one-line action layer summary to `state/audit.md`:

```
{date} | actions | queued: N | approved: N | executed: N | rejected: N | expired: N | pii_blocked: N
```

Also surface in the catch-up briefing footer (always, even if all zeros):

```
## ⚡ Action Layer
- Queued this session: N
- Approved / Executed: N / N
- Rejected / Expired: N / N
- PII blocks: N
```

If `executed > 0`, list each executed action with its undo window (if applicable):
```
  ✅ email_send → sent to landlord@example.com  [undo: 30s window closed]
  ✅ reminder_create → "File taxes" due Apr 15  [not reversible]
```

### Step 19b — Coaching nudge

Present the coaching nudge selected by `coaching_engine.py` at Step 8 (`--goals-file state/goals.md --format json`). Generate a 2-line coaching message matching the user's preferred style from `memory.md → coaching_preferences`. On weekly summary days, fold the nudge into the Goal Review section (§8.X in `config/briefing-formats.md`). If `coaching_engine.py` returns `suppressed: true` or exits non-zero, skip silently (UX-1).
Max 2 lines in standard mode, omit in flash mode.

If no active sprint exists and `python3 scripts/planning_signals.py sprint-triggers`
returns a `bootstrap_signal_id`, surface that signal through the Step 8t materialization
offer flow. Sprint writes must go through `python3 scripts/planning_signals.py materialize
SIG-NNN`, which delegates to `goals_writer.py --add-sprint`; do not append directly to
`state/goals.md`.

---

## 🔌 Connector & Token Health (MANDATORY — always include)

**This section MUST appear at the end of EVERY briefing, including flash mode.**
**Even when all connectors are healthy, show the full table (builds trust).**

Template — generate this table using actual connector status from Step 0 preflight:

```markdown
### 🔌 Connector & Token Health

| Connector | Status | Impact if Offline | Fix Command |
|-----------|--------|-------------------|-------------|
| Gmail | ✅ Online | — | — |
| Google Calendar | ✅ Online | — | — |
| Microsoft Graph | ⚠️ Token expires in 3m | Outlook mail/To Do missing | Auto-refreshing... |
| iCloud Mail | ⛔ Network blocked | iCloud mail not processed | Run from Mac terminal |
| Microsoft Graph | ⛔ OFFLINE (token exp 3d ago) | Outlook data missing from briefing | `python scripts/setup_msgraph_oauth.py --reauth` |
| Slack | ✅ Online | — | — |

**[N connectors offline — [data type] missing from this briefing. Fix commands above are copy-pasteable.]**
```

**Rules (non-negotiable):**
1. ALWAYS present — every briefing, even all-green
2. Impact column required — user must understand WHAT they're missing
3. Fix column required — exact command, copy-pasteable from Mac terminal
4. Summary line after table — count offline connectors + what data is affected
5. In read-only mode: add `📍 Read-only mode. State not updated. Run from Mac to persist.`
6. If preflight was skipped: add `⚠️ Preflight not run — connector status may be incomplete`

**Why this is mandatory:** Silent connector failures leave users with incomplete briefings that
look complete. The March 15 diagnostic showed MS Graph offline for days without the user knowing.
This block makes connector health impossible to miss.

## Skip conditions
- Steps 7, 7b, 14, 15, 16, 17, 18, 19: Skip in read-only mode (log each)
- Step 14: Skip if `briefing.email_enabled: false`
- Step 15: Skip if `integrations.microsoft_graph.todo_sync: false`
- Step 19b: Skip if `domains.goals.enabled: false`
- Connector Health block: **NEVER SKIP** — required in every briefing

## Error handling
- Email send failures: log, don't block catch-up success
- Re-encryption failures: loud warning, catch-up still completes
- All other finalize steps: non-blocking

---
## ✅ Catch-Up Complete

Display: `Artha catch-up complete. [N] emails → [N] actionable items. Next recommended catch-up: [time].`

If read-only mode: append `📍 Read-only mode. State not updated. Run from Mac terminal to persist.`
