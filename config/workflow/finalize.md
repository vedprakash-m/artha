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

For each domain signal surfaced in Step 12 (P0/P1 alerts), run the action composer:

```python
from scripts.action_composer import ActionComposer
from scripts.action_executor import ActionExecutor
from pathlib import Path

artha_dir = Path(".")
composer = ActionComposer(artha_dir=artha_dir)
executor = ActionExecutor(artha_dir)

for signal in domain_signals:          # signals collected during Step 12
    proposal = composer.compose(signal)
    if proposal is not None:
        try:
            action_id = executor.propose_direct(proposal)
            log(f"[action] queued {proposal.action_type} → {action_id[:8]}…")
        except Exception as e:
            log(f"[action] propose failed for {proposal.action_type}: {e}")
```

Workflow sequences (e.g., address_change, tax_prep) use `composer.compose_workflow(trigger, context)`.

**Constraints (non-negotiable):**
- Do NOT auto-execute anything — only propose to the queue
- High-friction actions (immigration, estate) always get `friction: high`
- PII guard fires at enqueue time; block silently if PII detected in params
- Duplicate proposals for the same (action_type, domain) within 24h are silently dropped

### Step 13 — Propose write actions

For each recommended action from domain processing:
- Present using Action Proposal Format (§9 in Artha.md)
- All write actions require explicit human approval before execution
- Actions: send_email, add_calendar_event, todo_sync, update_state

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

Call the script to atomically update `state/health-check.md` frontmatter and
rotate connector health logs older than 7 days to `tmp/connector_health_log.md`:

```bash
python3 scripts/health_check_writer.py \
    --last-catch-up "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --email-count N \
    --domains-processed domain1,domain2 \
    --mode normal
```

Replace `N` with the actual email count and the comma-separated list with
domains processed this session.  `--mode` is one of: `normal`, `degraded`,
`offline`, `read-only`.

The script is idempotent — safe to call multiple times; each call increments
`catch_up_count` by 1 and replaces bootstrap stubs with the proper schema.

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

### Step 19 — Accuracy calibration check

**SKIP in read-only mode** → log `⏭️ Step 19 skipped — read-only mode`

Compare today's extraction with yesterday's/last-catch-up state.
Flag contradictions or reversals (e.g., status: approved → pending).
Note uncertainty if extraction differs significantly.

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

If `domains.goals.enabled: true`: suggest one actionable nudge from goal sprint data.
Max 2 lines in standard mode, omit in flash mode.

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
