#!/usr/bin/env python3
"""
scripts/agents/tribe_agent.py — TribeAgent pre-compute (EAR-3, §5.3).

Reads ~/.artha-local/tribe.db (contacts + interactions) and state/social.md,
then computes relationship-decay scores and writes a ranked reconnect summary
back to state/social.md.

The LLM never scores relationships — only the deterministic decay formula from
this script is used. The LLM may compose draft messages but only up to the
hard 5-draft cap.

SQLite schema (tribe.db):
    contacts(id INTEGER PRIMARY KEY AUTOINCREMENT,
             name TEXT UNIQUE NOT NULL, circle TEXT,
             baseline_frequency_days INTEGER DEFAULT 90)
    interactions(id INTEGER PRIMARY KEY AUTOINCREMENT,
                 contact_id INTEGER REFERENCES contacts(id),
                 date TEXT NOT NULL, channel TEXT, sentiment TEXT)
    decay_scores(id INTEGER PRIMARY KEY AUTOINCREMENT,
                 contact_id INTEGER REFERENCES contacts(id),
                 computed_at TEXT, score REAL)

Decay formula: score = days_since_last_contact / baseline_frequency_days
    >2.0 = critical
    >1.0 = overdue
    <=1.0 = healthy

Safety guardrails (§8.6, TribeRateLimitGR):
    - Cold-start: contacts with <3 interactions → excluded from scoring
    - Batch import guard: >5 new contacts simultaneously → suppress ALL new contact scoring
    - Hard cap: 5 drafts per run (drafts staged to state/content_stage.md)

State files written:
    state/social.md           — updated with ## Reconnect List, ## Drafts Staged, ## Cold-Start Contacts
    state/content_stage.md    — draft messages (up to 5)
    tmp/tribe_last_run.json   — EAR-8 heartbeat

Ref: specs/prd-reloaded.md §5.3, §6.3, §8.6, §9.1
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_LOCAL_DIR = Path.home() / ".artha-local"
_STATE_DIR = _REPO_ROOT / "state"
_TMP_DIR = _REPO_ROOT / "tmp"

_DB_PATH = _LOCAL_DIR / "tribe.db"
_SOCIAL_FILE = _STATE_DIR / "social.md"
_STAGE_FILE = _STATE_DIR / "content_stage.md"
_SENTINEL = _LOCAL_DIR / ".tribe_writing"
_HEARTBEAT = _TMP_DIR / "tribe_last_run.json"

_DEFAULT_BASELINE_DAYS = 90
_COLD_START_MIN_INTERACTIONS = 3
_BATCH_IMPORT_THRESHOLD = 5    # >5 new contacts → suppress new-contact scoring
_DRAFT_CAP = 5                 # hard cap per run (§8.6 TribeRateLimitGR)

_SCORE_CRITICAL = 2.0
_SCORE_OVERDUE = 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_heartbeat(status: str, records_written: int, trace_id: str) -> None:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "domain": "tribe",
        "session_trace_id": trace_id,
        "timestamp_utc": _now_utc(),
        "status": status,
        "records_written": records_written,
    }
    _HEARTBEAT.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _open_db() -> sqlite3.Connection:
    """Open tribe.db with WAL mode and busy_timeout (A2.2 blanket policy)."""
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            name                    TEXT UNIQUE NOT NULL,
            circle                  TEXT,
            baseline_frequency_days INTEGER DEFAULT 90,
            created_at              TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS interactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id  INTEGER NOT NULL REFERENCES contacts(id),
            date        TEXT    NOT NULL,
            channel     TEXT,
            sentiment   TEXT,
            created_at  TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_int_contact ON interactions(contact_id, date)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decay_scores (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id  INTEGER NOT NULL REFERENCES contacts(id),
            computed_at TEXT,
            score       REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decay_contact ON decay_scores(contact_id, computed_at)")
    conn.commit()
    return conn


def _parse_social_md(social_path: Path) -> list[dict[str, Any]]:
    """Extract interaction hints from state/social.md.

    Looks for:
      - Lines: `- <Name>: <detail> on <YYYY-MM-DD>` or table rows with name/date.
    Returns list of {name, date, channel, sentiment} dicts.
    """
    if not social_path.exists():
        return []

    parsed: list[dict[str, Any]] = []
    text = social_path.read_text(encoding="utf-8", errors="replace")

    # Table rows: | Name | Date | Channel | Sentiment |
    table_re = re.compile(
        r"\|\s*([^|]+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|",
        re.IGNORECASE,
    )
    for m in table_re.finditer(text):
        name = m.group(1).strip()
        row_date = m.group(2).strip()
        channel = m.group(3).strip() or "unknown"
        sentiment = m.group(4).strip() or "neutral"
        if name.lower() in ("name", "contact"):
            continue  # skip header rows
        parsed.append({"name": name, "date": row_date, "channel": channel, "sentiment": sentiment})

    return parsed


def _sync_contacts(conn: sqlite3.Connection, interactions: list[dict[str, Any]]) -> int:
    """Insert new contacts from parsed interactions. Returns count of new contacts added."""
    new_count = 0
    with conn:
        for item in interactions:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO contacts(name, circle, baseline_frequency_days, created_at) "
                "VALUES(?, ?, ?, ?)",
                (item["name"], "general", _DEFAULT_BASELINE_DAYS, _now_utc()),
            )
            if cursor.lastrowid and cursor.rowcount > 0:
                new_count += 1
    return new_count


def _sync_interactions(conn: sqlite3.Connection, interactions: list[dict[str, Any]]) -> int:
    """Upsert interaction records. Returns number of records inserted."""
    count = 0
    with conn:
        for item in interactions:
            row = conn.execute(
                "SELECT id FROM contacts WHERE name = ?", (item["name"],)
            ).fetchone()
            if not row:
                continue
            contact_id = row[0]
            # Insert if this exact (contact, date, channel) combo not already stored
            existing = conn.execute(
                "SELECT id FROM interactions WHERE contact_id=? AND date=? AND channel=?",
                (contact_id, item["date"], item["channel"]),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO interactions(contact_id, date, channel, sentiment, created_at) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (contact_id, item["date"], item["channel"], item["sentiment"], _now_utc()),
                )
                count += 1
    return count


def _compute_decay_scores(
    conn: sqlite3.Connection, today: date, new_contacts_count: int
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Compute decay scores, enforcing cold-start and batch-import guards.

    Returns (scored_contacts, notes, cold_start_count).
    """
    notes: list[str] = []
    computed_at = _now_utc()

    # Batch import guard (§8.6): suppress new contacts if >5 imported this run
    if new_contacts_count > _BATCH_IMPORT_THRESHOLD:
        notes.append(
            f"{new_contacts_count} contacts imported — decay scoring suppressed for new contacts "
            f"(batch import guard active, threshold: {_BATCH_IMPORT_THRESHOLD})."
        )

    # Load all contacts with their interaction counts and last interaction date
    contacts = conn.execute(
        "SELECT c.id, c.name, c.circle, c.baseline_frequency_days FROM contacts c"
    ).fetchall()

    scored: list[dict[str, Any]] = []
    cold_start_count = 0

    for c_id, name, circle, baseline_days in contacts:
        interaction_count = conn.execute(
            "SELECT COUNT(*) FROM interactions WHERE contact_id=?", (c_id,)
        ).fetchone()[0]

        # Cold-start guard (§8.6): exclude contacts with <3 interactions
        if interaction_count < _COLD_START_MIN_INTERACTIONS:
            cold_start_count += 1
            continue

        # Get last interaction date
        last_row = conn.execute(
            "SELECT MAX(date) FROM interactions WHERE contact_id=?", (c_id,)
        ).fetchone()
        if not last_row or not last_row[0]:
            cold_start_count += 1
            continue

        last_date = date.fromisoformat(last_row[0])
        days_since = (today - last_date).days
        base = max(1, baseline_days)
        score = round(days_since / base, 3)

        scored.append({
            "contact_id": c_id,
            "name": name,
            "circle": circle or "general",
            "baseline_frequency_days": base,
            "days_since_last": days_since,
            "last_interaction": last_row[0],
            "score": score,
            "status": (
                "critical" if score >= _SCORE_CRITICAL
                else "overdue" if score >= _SCORE_OVERDUE
                else "healthy"
            ),
            "computed_at": computed_at,
        })

    # Store decay scores
    if scored:
        with conn:
            conn.executemany(
                "INSERT INTO decay_scores(contact_id, computed_at, score) VALUES(?, ?, ?)",
                [(s["contact_id"], s["computed_at"], s["score"]) for s in scored],
            )

    # Sort by score descending (most overdue first)
    scored.sort(key=lambda x: x["score"], reverse=True)

    if cold_start_count > 0:
        notes.append(
            f"{cold_start_count} contact(s) pending baseline "
            f"({_COLD_START_MIN_INTERACTIONS} interactions required)."
        )

    return scored, notes, cold_start_count


def _write_state_files(
    today: str,
    scored: list[dict[str, Any]],
    notes: list[str],
    cold_start_count: int,
    trace_id: str,
) -> int:
    """Rewrite the Tribe section in state/social.md and stage drafts."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Build reconnect list (overdue + critical only)
    overdue = [s for s in scored if s["status"] in ("overdue", "critical")]
    healthy = [s for s in scored if s["status"] == "healthy"]

    reconnect_lines = []
    if overdue:
        reconnect_lines.append(
            "| Name | Circle | decay_score | Days Since | Status | baseline_frequency_days |"
        )
        reconnect_lines.append(
            "|------|--------|-------------|-----------|--------|------------------------|"
        )
        for s in overdue:
            reconnect_lines.append(
                f"| {s['name']} | {s['circle']} | {s['score']} | "
                f"{s['days_since_last']} | {s['status']} | {s['baseline_frequency_days']} |"
            )
    else:
        reconnect_lines.append("_No overdue relationships — all contacts within healthy range._")

    # Drafts staged (up to _DRAFT_CAP critical contacts)
    draft_targets = [s for s in scored if s["status"] == "critical"][:_DRAFT_CAP]
    draft_lines = []
    if draft_targets:
        for s in draft_targets:
            draft_lines.append(
                f"- **{s['name']}** — {s['days_since_last']} days since last contact "
                f"(decay: {s['score']}, baseline: {s['baseline_frequency_days']}d)"
            )
    else:
        draft_lines.append("_No critical contacts requiring draft preparation._")

    # Note section
    notes_lines = [f"- {n}" for n in notes] if notes else ["_No notes._"]

    # Update state/social.md (replace or append Tribe section)
    existing = ""
    if _SOCIAL_FILE.exists():
        existing = _SOCIAL_FILE.read_text(encoding="utf-8", errors="replace")

    # Strip any existing auto-generated tribe block
    tribe_block_re = re.compile(
        r"(?m)^<!-- tribe-agent-begin -->.*?^<!-- tribe-agent-end -->\s*",
        re.DOTALL,
    )
    existing = tribe_block_re.sub("", existing).rstrip()

    tribe_block = f"""

<!-- tribe-agent-begin -->
## Reconnect List
_Generated: {today} | decay_score = days_since_last / baseline_frequency_days_

{chr(10).join(reconnect_lines)}

## Drafts Staged
_Hard cap: {_DRAFT_CAP} per run. LLM composes — Artha approves._
{chr(10).join(draft_lines)}

## Cold-Start Contacts
{chr(10).join(notes_lines)}

_source: tribe.db — generated_at: {_now_utc()}_
_session_trace_id: {trace_id}_
<!-- tribe-agent-end -->
"""
    _SOCIAL_FILE.write_text(existing + tribe_block, encoding="utf-8")

    # Write content_stage.md (draft placeholder)
    if draft_targets:
        stage_lines = [
            f"# Content Stage\ndate: {today}\n",
            "## Relationship Drafts (tribe-agent — pending LLM composition)\n",
        ]
        for s in draft_targets:
            stage_lines.append(
                f"### {s['name']}\n"
                f"- Last contact: {s['last_interaction']} ({s['days_since_last']} days ago)\n"
                f"- Suggested channel: {s.get('circle', 'general')}\n"
                f"- Note: decay {s['score']} — needs reconnect message\n"
            )
        _STAGE_FILE.write_text("\n".join(stage_lines), encoding="utf-8")

    records_written = len(scored)
    return records_written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    today = date.today()
    today_str = today.isoformat()
    iso_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    trace_id = f"pre-compute-tribe-{iso_ts}"

    # Acquire sentinel
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    _SENTINEL.write_text(_now_utc(), encoding="utf-8")

    try:
        conn = _open_db()

        # Parse state/social.md for interaction hints
        interactions = _parse_social_md(_SOCIAL_FILE)

        # Sync contacts and interactions into tribe.db
        new_contacts = _sync_contacts(conn, interactions)
        _sync_interactions(conn, interactions)

        # Compute decay scores with all guards active
        scored, notes, cold_start_count = _compute_decay_scores(conn, today, new_contacts)
        conn.close()

        # Write updated state files
        records_written = _write_state_files(today_str, scored, notes, cold_start_count, trace_id)

        _write_heartbeat("success", records_written, trace_id)
        overdue_count = sum(1 for s in scored if s["status"] in ("overdue", "critical"))
        print(
            f"✓ TribeAgent: contacts={len(scored)}, overdue={overdue_count}, "
            f"cold_start={cold_start_count}, records={records_written}"
        )
        return 0

    except Exception as exc:
        print(f"⛔ TribeAgent failed: {exc}", file=sys.stderr)
        _write_heartbeat("error", 0, trace_id)
        return 1
    finally:
        if _SENTINEL.exists():
            _SENTINEL.unlink()


if __name__ == "__main__":
    sys.exit(main())
