# pii-guard: ignore-file — module init; no personal data
"""scripts/agents/ — Pre-compute domain agent scripts (EAR-3).

Each script is invoked by precompute.py on a cron schedule.
Scripts are stateless, idempotent, and write dedicated heartbeat files
to tmp/{domain}_last_run.json after each run (§9.1, R5/R6).

Blanket policy (A2.2): every SQLite open in this package must execute
    PRAGMA journal_mode=WAL;
    PRAGMA busy_timeout=5000;
as the first two statements after connection.
"""
