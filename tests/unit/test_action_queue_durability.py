"""WP-03 / RA-05 durability: a power loss must not erase an acknowledged claim or outcome."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import action_queue  # noqa: E402

SYNCHRONOUS_FULL = 2   # SQLite: 0 OFF, 1 NORMAL, 2 FULL, 3 EXTRA


def test_connections_use_synchronous_full(tmp_path):
    conn = action_queue._open_db(tmp_path / "a.db")
    try:
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == SYNCHRONOUS_FULL
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
