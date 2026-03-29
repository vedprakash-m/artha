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

When pipeline.py is run, it automatically calls `scripts/email_classifier.py`
for each connector's email records, tagging them with `marketing: true|false`.
Records with `marketing: true` should be suppressed and NOT routed in Step 6.

If using AI CLI without pipeline.py, apply these rules manually:
Immediately discard emails matching:
- Known marketing sender domains: `@promotions.google.com`, `@e.amazon.com`, `*.bulk-mailer.*`
- Subject: "unsubscribe", "20% off", "sale ends", "limited time offer", "flash sale"
- Header: `List-Unsubscribe:` present AND sender is not a trusted domain

Trusted domains (never suppress): USCIS, IRS, financial institutions, your employer,
your immigration attorney, medical insurance providers, government agencies.
(Configure personal domains in `artha_config.yaml` → `email_classifier.whitelist_domains`.)

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

**5d — Context offloading (pipeline mode):**
After pre-processing, offload the bulk pipeline output to reduce context pressure:
```bash
python3 -c "
from scripts.context_offloader import offload_artifact
offload_artifact('pipeline_output', pipeline_jsonl_str)
"
```
In AI CLI mode: write the processed email batch to `tmp/pipeline_output.jsonl` and
reference it by path throughout Steps 6–9 rather than holding it in context.

### Step 6 — Route emails to domains

Apply routing table (§3 / `config/routing.yaml`).
Unknown senders: classify by content.
Marketing emails (Step 5a survivors): suppress, do not route.
Emails may route to multiple domains.

### Step 6b — Domain loading strategy (lazy loading)

**Tier A (always-load):** `calendar`, `comms`, `goals`, `finance`, `immigration`, `health`
**Tier B (lazy-load):** All other enabled domains — load ONLY if at least one email was routed here in Step 6.

**Skill cache location:** Skills results are now persisted at `state/skills_cache.json` (not `tmp/`). This file is synced via OneDrive and survives Step 18 cleanup. The cache carries per-skill health counters (`health.classification`, `health.consecutive_zero`, etc.) used by R7 cadence reduction and `/eval skills`.

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
   - New item: append with schema `OI-NNN`, date_added, source_domain, description, deadline, priority, status: open, **`origin: system`**
3. Use sequential OI-NNN ids (read current max, increment by 1)
4. Write complete file atomically

### Step 7c — Open Items Staleness Validation (MANDATORY)

**Before surfacing ANY open item in the briefing, verify it is still open.**

This step prevents stale/completed items from cluttering the briefing. Without it,
items that were resolved days ago keep appearing as "open" because the state file
was never updated.

**Validation protocol:**
1. Read ALL items with `status: open` from `state/work/work-open-items.md`
2. For items older than 3 days (`date_added` or `last_seen` > 3d ago):
   - Ask WorkIQ: *"Has this item been resolved? [item description]"*
   - OR check: did the user close this in a subsequent session?
   - OR check: does the evidence trail show completion (email sent, meeting held, PR merged)?
3. **Classify each item:**
   - ✅ **DONE** — evidence of completion found → mark `status: done`, add `completed_date`
   - ⏳ **STILL OPEN** — no evidence of completion → keep `status: open`
   - ❓ **STALE/UNCERTAIN** — item is >7 days old with no activity signal →
     mark `status: stale`, show as: `❓ [STALE] [description] — may be outdated, verify`
4. **Briefing display rules:**
   - Only show items with status `open` or `stale` (with stale marker)
   - DONE items move to `## Completed` section (not deleted — audit trail)
   - Show count: "30 items checked: 12 still open, 15 done, 3 stale"

**WorkIQ budget:** Max 5 validation queries per catch-up session. Prioritize:
oldest items first, then items with `ASAP` due dates.

### Step 7d — Open Items Ownership Validation (MANDATORY)

**Before surfacing ANY open item as YOUR action, verify YOU are the actual owner.**

This is the #1 source of false alerts: Artha treats "you're in the meeting/thread"
as "it's your action item." Many items belong to teammates who are driving them.

**Ownership check protocol:**
1. For each item with `status: open`, check the `Owner` field in `work-open-items.md`
2. If Owner = Ved → surface as your action
3. If Owner = someone else → reclassify as:
   `👀 [WATCHING] [description] — DRI: [owner], you are informed`
4. If Owner = Ved but the ask was directed at someone else (check original thread) →
   reclassify per the actual ball-holder

**Known ownership delegations (learned from user corrections):**
- **[Networking DRI]** — DRI for ALL networking/VNet topics: LSO, kRDMA, RDMA
  connectivity, NMAgent watchdog, Hypernet VNet Service Tags, IPv6 decisions
- **[Program DRI]** — DRI for program structure, execution & reporting
- **[Buildout DRI]** — DRI for buildout requirements, one-pager

**Display rules:**
- YOUR actions: show in 🔴/🟡 sections with full detail
- WATCHING items: show in a separate `👀 Tracking (not your action)` section
- Count: "12 items: 5 YOUR actions, 7 watching"

**Why this matters:** Surfacing other people's action items as yours creates false
urgency, wastes triage time, and erodes trust. The user should never have to say
"that's not my action."

**Why this matters:** Without validation, the open items list grows monotonically —
items are added but never removed, creating a "wall of stale tasks" that erodes
trust in the briefing. The user should never have to say "that's already done."

**`items_surfaced` counter:** Start a running count of P0/P1/P2 alerts generated during Steps 7–7b. Increment by 1 for each alert added to the briefing buffer. Pass this count to `health_check_writer.py --items-surfaced` at Step 16. (Counter continues in Step 8 — see `reason.md`.)

**Note:** `origin: system` marks auto-extracted OIs. User-requested OIs get `origin: user` at Step 19. `source_domain:` continues to record which domain the item came from (e.g., `source_domain: finance`). The two fields answer different questions: `origin` = who created it, `source_domain` = where it came from.

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
