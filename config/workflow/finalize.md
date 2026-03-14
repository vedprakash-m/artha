# Phase 5 — Finalize (Steps 12–19b)

## Steps

### Step 12: Surface active alerts
- Scan all domains for P0/P1 alerts
- Format with severity indicators: red circle, orange circle, yellow circle

### Step 13: Propose write actions
- Based on processed data, propose actions from `config/actions.yaml`
- All write actions require human approval (no auto-send)
- Actions: send_email, add_calendar_event, todo_sync

### Step 14: Email briefing
- If `briefing.email_enabled: true`, send via `scripts/gmail_send.py`
- If `briefing.spouse_filtered: true`, generate filtered copy

### Step 15: Push new items to Microsoft To Do
- If `integrations.microsoft_graph.todo_sync: true`:
  - Run `python scripts/todo_sync.py push`
  - Sync open_items.md → Microsoft To Do lists

### Step 16: Update health-check
- Write catch-up metadata to `state/health-check.md`
- Fields: last_catch_up timestamp, domains processed, email count,
  data sources used, warnings, errors

### Step 17: Log PII stats
- Record PII filter stats to `state/audit.md`
- Counts: items scanned, items flagged, items redacted, by type

### Step 18: Ephemeral cleanup + Re-encrypt
- Clear `tmp/` directory (ephemeral cache)
- Run `python scripts/vault.py encrypt` to re-encrypt sensitive state
- Verify lock file `.artha-decrypted` is removed

### Step 19: Accuracy calibration check
- Compare today's extraction with yesterday's state
- Flag contradictions or reversals (e.g., status went from "approved" back to "pending")
- Confidence calibration: if extraction differs significantly, note uncertainty

### Step 19b: Coaching nudge
- Based on goal sprint data, suggest one actionable nudge
- Only if `domains.goals.enabled: true`

## Skip conditions
- Step 14: Skip if `briefing.email_enabled: false`
- Step 15: Skip if `integrations.microsoft_graph.todo_sync: false`
- Step 19b: Skip if `domains.goals.enabled: false`

## Error handling
- Email send failures are logged but don't affect catch-up success
- Re-encryption failures produce a loud warning (data left decrypted)
- All other steps in this phase are non-blocking
