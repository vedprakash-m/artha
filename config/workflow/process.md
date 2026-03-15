---
phase: process
steps: 5–7b
source: config/Artha.core.md §2, Steps 5–7b
---

## ⛩️ PHASE GATE — Process

**If running /catch-up and you haven't loaded this file yet, STOP and read it now.**

**Before executing this phase, verify:**
- [ ] Fetch phase complete (pipeline.py ran or MCP artha_fetch_data called)
- [ ] Tier A state files loaded (health-check.md, open_items.md, memory.md, comms.md, calendar.md, goals.md)
- [ ] Email volume tier is known (from Step 4d)

### CRITICAL — Email Body Requirement

You MUST read the **full body** of the top 10 highest-priority emails
(priority order: 🔴 Critical > 🟠 Urgent > 🟡 Standard > 🔵 Low).

**Processing from search snippets alone is EXPLICITLY PROHIBITED.**

If a full email body cannot be read:
- Flag every snippet-derived data point: `[snippet — verify in email client]`
- Log: `⚠️ Email [N] body unavailable — processed from snippet`
- Do NOT fabricate content from partial subject lines

**If ANY prerequisite is not met, STOP and complete it first.**

---

## Steps

### Step 5 — PII pre-filter + email pre-processing

**5a — Marketing suppression (run first, before PII scan):**
Immediately discard emails matching:
- Known marketing sender domains: `@promotions.google.com`, `@e.amazon.com`, `*.bulk-mailer.*`
- Subject: "unsubscribe", "20% off", "sale ends", "limited time offer", "flash sale"
- Header: `List-Unsubscribe:` present AND sender is not a trusted domain

**5b — PII scan:**
```bash
python scripts/pii_guard.py scan
```
If PII detected: redact per §4 rules, log to `state/audit.md`. **Never persist raw PII.**

**Skip Step 5b in read-only mode if pipeline.py could not run.**
Note in footer: "PII scan: limited (MCP-direct data, no pipeline filtering)."

**5c — Content preparation:**
1. Strip HTML, convert to plain text
2. Remove quoted/forwarded history beyond most-recent reply
3. Strip standard footers
4. Truncate each body to 1,500 tokens — append `[…truncated]` if cut
5. Batch-summarize if digest mode or >50 emails

### Step 6 — Route emails to domains

Apply routing table (§3 / `config/routing.yaml`).
Unknown senders: classify by content.
Marketing emails (Step 5a survivors): suppress, do not route.
Emails may route to multiple domains.

### Step 6b — Domain loading strategy (lazy loading)

**Tier A (always-load):** `calendar`, `comms`, `goals`, `finance`, `immigration`, `health`
**Tier B (lazy-load):** All other enabled domains — load ONLY if at least one email was routed here in Step 6.

### Step 7 — Process domains (IN PARALLEL where possible)

**Skip all domain writes in read-only mode** → log `⏭️ Step 7 skipped — read-only mode`

For each domain with new emails/events:
a. Load domain prompt from `prompts/<domain>.md` (+ overlay if `config/prompt-overlays/<domain>.md` exists)
b. Load current state from `state/<domain>.md`
c. Extract structured data per prompt instructions
d. Apply Layer 2 semantic redaction (§4) before writing
e. Check for duplicate entries (same source + same item ID = update in place)
f. **Net-negative write guard:** if proposed write removes >20% of existing fields → HALT, surface diff, wait for user confirmation
g. Write `state/<domain>.md` atomically
h. Collect briefing contribution (1–5 bullets per domain)

**Post-write verification (after each successful write):**
- File exists and is non-empty (>100 bytes)
- First line is `---` (valid YAML frontmatter)
- `domain:` field present and matches expected domain
- `last_updated:` field present with valid ISO-8601 timestamp

### Step 7b — Update open_items.md

**Skip in read-only mode** → log `⏭️ Step 7b skipped — read-only mode`

1. Read `state/open_items.md`
2. For each actionable item (🔴 or 🟠 rated, or `action_required: true`):
   - Deduplicate: if same description + deadline + status: open → update `last_seen` only
   - New item: append with schema `OI-NNN`, date_added, source_domain, description, deadline, priority, status: open
3. Use sequential OI-NNN ids (read current max, increment by 1)
4. Write complete file atomically

## Error handling
- PII detection on unresolvable content = skip that email, log to audit.md
- Domain processing errors = log to audit.md, continue other domains
- Net-negative guard = block write, surface diff, await user decision
- Read-only mode = skip all writes, continue reading

**Checkpoint (Steps 7–7b complete):** After domain processing and open_items update, write:
```bash
python -c "from scripts.checkpoint import write_checkpoint; from pathlib import Path; write_checkpoint(Path('.'), 7, domains_processed=['finance','immigration','...'])"
```
Replace the list with the actual domains processed this session.

---
## ✅ Phase Complete — Transition
→ **Load `config/workflow/reason.md` now.** Do NOT proceed without it.
