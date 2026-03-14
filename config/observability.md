## §13 Observability & Retrospective *(v2.1)*

### Post-session calibration (Step 19 details)
The calibration system learns over time:
- Questions get more targeted as Artha builds domain accuracy history
- Skip rate tracked per user: if consistently skipped, frequency auto-reduces
- Correction patterns logged to memory.md feed back into domain extraction logic

### Monthly retrospective
Generated on the 1st of each month if last retro was >28 days ago (Step 3 trigger).
Saved to `summaries/YYYY-MM-retro.md`. The format is §8.10.
A brief 3-line summary is embedded in the monthly 1st catch-up briefing footer:
`📊 Monthly retro saved: [N] catch-ups · [acceptance_rate]% action rate · [signal:noise]% signal`

### `/diff` implementation notes
The `/diff` command uses git log on the `state/` directory. To enable:
```bash
git init        # if not already a git repo
git add state/
git commit -m "Artha state baseline [date]"
```
Artha automatically commits state/ after each `vault.py encrypt` cycle (Step 18) if git is configured:
```bash
git add state/*.md state/*.md.age && \
git commit -m "Artha catch-up [ISO datetime]" --quiet || true
```
Non-blocking: if git fails, continue without committing. Log git commit status to health-check.md.

### Performance metrics (v4.1)

The fetch and skill phases collect per-component wall-clock timings:

| Metric file | Writer | Retention |
|------------|--------|-----------|
| `tmp/pipeline_metrics.json` | `scripts/pipeline.py` | Last 50 runs |
| `tmp/skills_metrics.json` | `scripts/skill_runner.py` | Last 50 runs |
| `tmp/catchup_metrics.json` | `scripts/lib/metrics.py` | Last 30 runs |

**Pipeline parallelism:** `pipeline.py` launches all enabled connectors in a
`ThreadPoolExecutor` (max 8 threads). Each connector runs in its own thread;
JSONL output is buffered per-connector and flushed sequentially after all finish.

### Eval runner

`scripts/eval_runner.py` aggregates all metric files and health-check data into
a single evaluation report:

```bash
python scripts/eval_runner.py                # Full report
python scripts/eval_runner.py --perf         # Performance only
python scripts/eval_runner.py --accuracy     # Accuracy & signal quality
python scripts/eval_runner.py --freshness    # Domain staleness
python scripts/eval_runner.py --json         # Machine-readable output
python scripts/eval_runner.py --trend 14     # 14-day trend window
```

Dimensions analyzed:
- **Performance** — avg/min/max/p95 per connector, per skill, per phase; trend detection (improving/degrading/stable)
- **Accuracy** — action acceptance rate (7-day rolling), per-domain accuracy, signal:noise ratio
- **Freshness** — stale domains (>7 days), OAuth token health, bootstrap coverage

---

### Channel Bridge audit events (v5.0)

All channel push / listener events are appended to `state/audit.md` in standard format:
`[ISO-8601] EVENT_TYPE | key: value | key: value`

| Event | Emitted by | Key fields |
|-------|-----------|------------|
| `CHANNEL_PUSH` | `channel_push.py` | `channel`, `recipient`, `chars`, `scope`, `pii_filtered` |
| `CHANNEL_PUSH_SKIPPED` | `channel_push.py` | `channel`, `marker_host`, `marker_time` |
| `CHANNEL_PUSH_FAIL` | `channel_push.py` | `channel`, `error_type`, `message` |
| `CHANNEL_PUSH_PENDING` | `channel_push.py` | `channel`, `recipient`, `pending_file` |
| `CHANNEL_PENDING_EXPIRED` | `channel_push.py` | `channel`, `file`, `age_hours` |
| `CHANNEL_LISTENER_START` | `channel_listener.py` | `host`, `channels` |
| `CHANNEL_LISTENER_SKIP` | `channel_listener.py` | `host`, `designated_host` |
| `CHANNEL_IN` | `channel_listener.py` | `channel`, `sender` (name alias), `command` |
| `CHANNEL_OUT` | `channel_listener.py` | `channel`, `recipient`, `chars`, `pii_filtered` |
| `CHANNEL_REJECT` | `channel_listener.py` | `channel`, `sender` (raw ID), `reason` |
| `CHANNEL_RATE_LIMIT` | `channel_listener.py` | `channel`, `sender`, `cooldown_sec` |
| `CHANNEL_SESSION` | `channel_listener.py` | `channel`, `recipient`, `action` (unlock / expire) |
| `CHANNEL_ERROR` | either | `channel`, `error_type`, `message` |
| `CHANNEL_HEALTH` | `setup_channel.py` / preflight | `channel`, `healthy`, `latency_ms` |

**Privacy note:** The audit log records command names and alias names (from `recipients` config),
never raw message content, chat IDs, or bot tokens.

---

