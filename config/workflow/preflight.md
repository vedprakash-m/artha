---
phase: preflight
steps: 0–2b
source: config/Artha.core.md §2, Steps 0–2b
---

## ⛩️ PHASE GATE — Preflight

**If running /catch-up and you haven't loaded this file yet, STOP and read it now.**

**Before executing this phase, verify:**
- [ ] You are running `/catch-up` (or equivalent trigger)
- [ ] config/Artha.md has been loaded (contains §1 identity + §R router)

**If ANY prerequisite is not met, STOP and complete it first.**

---

## Steps

### Step 0 — Pre-flight Go/No-Go Gate

**This step runs BEFORE any data is touched. A failed gate = no catch-up.**

**0a — Environment detection (run first in VM/sandboxed environments):**
```bash
python scripts/detect_environment.py
```
Parse the JSON output. If `capabilities.filesystem_writable: false`, activate **read-only mode**:
- Prepend briefing with: `⚠️ READ-ONLY MODE — no state files updated this session`
- Use `--advisory` flag for Step 0b
- See Artha.core.md §1 Read-Only Environment Protocol for full instructions

**0b — Preflight gate:**
```bash
# Normal (local Mac/Linux):
python scripts/preflight.py

# Read-only environment (VM — after detect_environment shows fs_writable: false):
python scripts/preflight.py --advisory
```
- Exit 0 = all P0 checks pass → proceed
- Exit 0 (advisory) = P0 advisories logged, proceed with degraded catch-up
- Exit 1 = P0 check failed → halt: `⛔ Pre-flight failed: [check] — [error]. Fix before retrying.`
- Exit 3 = cold start (no user_profile.yaml) → route to first-run experience
- P1 warnings do NOT block — log to briefing footer
- Log gate result to `state/health-check.md` → `preflight_runs:`

**In read-only mode:** List each advisory in the briefing header:
```
⚠️ ADVISORY: vault.py health — age not installed (encrypted state inaccessible)  
⚠️ ADVISORY: state directory — read-only filesystem (no state writes this session)
```

**OAuth/Token dual-failure rule:** If MS Graph token is BOTH expired AND network is blocked,
report both separately (token fix is actionable from Mac even if network fix isn't).

### Step 1 — Decrypt sensitive state

**Skip in read-only mode** → log `⏭️ Step 1 skipped — read-only mode`

```bash
python scripts/vault.py decrypt
```
If `age` not installed or key not in credential store: log warning and continue.

### Step 1b — Pull To Do completion status

**Skip in read-only mode** → log `⏭️ Step 1b skipped — read-only mode`

If `.tokens/msgraph-token.json` present:
```bash
python scripts/todo_sync.py --pull
```
Marks items completed in Microsoft To Do as `status: done` in `state/open_items.md`.
If token missing: skip with note in briefing footer.

### Step 2 — Read health-check

Read `state/health-check.md`. Extract `last_catch_up` from frontmatter.
Calculate `hours_elapsed`. If >48h: prepend "⚠️ Last catch-up was [N] days ago."

In read-only mode: if health-check.md inaccessible, set `hours_elapsed = unknown`, use standard format.

### Step 2b — Digest Mode Check

```
if hours_elapsed < 4 (or unknown):    briefing_format = "flash", domain_item_cap = 2
if hours_elapsed > 48:                 briefing_format = "digest", email_batch_size = 20
else:                                   briefing_format = "standard", domain_item_cap = 5
```

User overrides: `/catch-up flash` | `/catch-up deep` | `/catch-up standard`

## Skip conditions
- Steps 1, 1b: skip + warn in read-only mode
- Step 2b: Never skip

## Error handling
- Step 0 strict failure = hard stop
- Step 0 advisory = continue with degraded briefing, labeled
- Steps 1, 1b = warn and continue
- Steps 2, 2b = use defaults and continue

---
## ✅ Phase Complete — Transition
→ **Load `config/workflow/fetch.md` now.** Do NOT proceed without it.
