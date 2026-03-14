# Phase 2 — Fetch (Steps 3–4d)

## Steps

### Step 3: Periodic triggers
- Check skills cadence via `python scripts/skill_runner.py`
- Skills: USCIS status, visa bulletin, NOAA weather, NHTSA recalls, property tax
- Cadence: daily / weekly / every_run (configured in skills.yaml)

### Step 4: Fetch (parallel — all sources simultaneously)
- Run `python scripts/pipeline.py --since <48h_ago>`
- Sources: Gmail, Outlook, iCloud, Google Calendar, MS Calendar, Canvas LMS
- Output: JSONL records to stdout
- Retry: exponential backoff with jitter (lib/retry.py)
- **Parallelism**: `pipeline.py` uses `ThreadPoolExecutor` (max 8 workers) to
  fetch all enabled connectors concurrently. Each connector runs in its own
  thread with isolated JSONL buffering; results are flushed sequentially to
  stdout after all threads complete to prevent interleaving.
- **Metrics**: Per-connector wall-clock timings are logged and persisted to
  `tmp/pipeline_metrics.json` (last 50 runs, append-only). Use
  `python scripts/eval_runner.py --perf` to view trends.

### Step 4b: Tiered Context Loading
- Tier 1 (always): immigration, finance, kids, health
- Tier 2 (if within context budget): home, vehicle, travel
- Tier 3 (on-demand): social, digital, learning, boundary
- Context budget: ~100k tokens across all state files

### Step 4c: Bootstrap State Detection
- If a domain's state file says "updated_by: bootstrap" → flag for user
- Suggest `/bootstrap <domain>` for unpopulated domains

### Step 4d: Email Volume Tier Detection
- < 20 emails: full analysis
- 20–50 emails: standard (skip low-priority senders)
- 50+ emails: triage mode (P0/P1 only, defer P2)

## Error handling
- Individual connector failures don't block other connectors
- Pipeline exit code 3 = partial success (some connectors errored)
- P0 skill failures (e.g., USCIS) = logged as critical but don't halt catch-up
