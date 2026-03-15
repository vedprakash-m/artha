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

Write catch-up metadata to `state/health-check.md` frontmatter:
```yaml
last_catch_up: YYYY-MM-DDTHH:MM:SSZ
catch_up_count: N+1
domains_processed: [list]
email_count: N
session_mode: normal|degraded|offline|read-only
```

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
# Clear ephemeral cache
rm -f tmp/*.json tmp/*.jsonl

# Re-encrypt sensitive state
python scripts/vault.py encrypt
```
Verify `.artha-decrypted` lock file is removed.
If re-encryption fails: LOUD warning — `⛔ Re-encryption failed — sensitive state left decrypted`.

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
