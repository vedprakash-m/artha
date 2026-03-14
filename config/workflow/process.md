# Phase 3 — Process (Steps 5–7b)

## Steps

### Step 5: PII pre-filter + email pre-processing
- Run `python scripts/pii_guard.py filter` on all fetched text
- Layer 1: regex-based detection (SSN, CC, ITIN, DL, passport, etc.)
- Allowlist: USCIS receipt numbers, Amazon orders, masked cards
- If PII found: redact and log to audit.md

### Step 6: Route emails to domains
- Use routing rules from `config/routing.yaml` (or routing.example.yaml)
- Routing is sender/subject pattern matching → domain assignment
- Unknown senders: classify by content using AI
- Marketing emails (unsubscribe signal): suppress

### Step 7: Process domains (parallel where possible)
- For each domain with new data:
  1. Load domain prompt from `prompts/<domain>.md`
  2. Load current state from `state/<domain>.md`
  3. Extract structured data per prompt instructions
  4. Apply Layer 2 semantic redaction (AI-driven)
  5. Write updated state (atomic — write to .tmp, then rename)
- Net-negative guard: if new state is >20% smaller, warn before writing

### Step 7b: Update open_items.md
- Extract action items from all processed domains
- Merge with existing open items (dedup by content hash)
- Auto-close items that are no longer relevant
- Format: OI-NNN with domain, priority, due date

## Error handling
- PII detection halts the entire pipeline (security-critical)
- Domain processing errors log to audit.md and continue
- Net-negative guard blocks the write but doesn't halt other domains
