# Phase 1 — Preflight (Steps 0–2b)

## Steps

### Step 0: Pre-flight Go/No-Go Gate
- Run `python scripts/preflight.py`
- Exit 0 = proceed; Exit 1 = halt; Exit 3 = cold start (route to bootstrap)
- Checks: OAuth token health, profile existence, age CLI, venv state

### Step 1: Decrypt sensitive state
- Run `python scripts/vault.py decrypt`
- If age not installed, continue with plaintext state files

### Step 1b: Pull To Do completion status
- Run `python scripts/todo_sync.py pull` (if MS Graph enabled)
- Merges completed items from Microsoft To Do into open_items.md

### Step 2: Read health-check
- Load `state/health-check.md` frontmatter for last catch-up timestamp
- Calculate hours since last catch-up

### Step 2b: Digest Mode Check
- If last catch-up > 72h, switch to digest mode (summarize instead of detail)
- Digest mode reduces email volume by grouping by domain

## Skip conditions
- Step 1b: Skip if `integrations.microsoft_graph.todo_sync: false`
- Step 2b: Never skip (informational only)

## Error handling
- Step 0 failure = hard stop (no catch-up)
- Steps 1, 1b = warn and continue
- Steps 2, 2b = use defaults and continue
