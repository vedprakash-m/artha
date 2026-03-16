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

After the session summary is generated, extract durable facts for cross-session learning.
`get_context_card()` in `session_summarizer.py` now automatically calls `fact_extractor`
for catch-up commands — no manual invocation needed when using the Python harness.

For manual invocation (AI CLI context or fallback):
```bash
python3 -c "
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
"
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
