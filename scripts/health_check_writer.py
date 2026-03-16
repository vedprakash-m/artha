#!/usr/bin/env python3
"""
scripts/health_check_writer.py — Atomic health-check frontmatter updater.

Updates state/health-check.md frontmatter with catch-up session metadata
and rotates old connector health log entries older than 7 days into
tmp/connector_health_log.md to keep health-check.md under roughly 100 lines.

Usage
-----
    python scripts/health_check_writer.py \\
        --last-catch-up 2026-03-15T23:35:00Z \\
        --email-count 21 \\
        --domains-processed finance,insurance,kids \\
        --mode normal|degraded|offline|read-only

All flags are optional; omitted values are left unchanged in the frontmatter.

Purpose
-------
Step 16 of finalize.md originally asked the AI to write the frontmatter
manually, which it often skipped under context pressure.  This script makes
the write deterministic and idempotent — safe to call multiple times.

Safety
------
- Uses the same vault lock guard (state/.artha-lock) as the main harness to
  prevent concurrent writes.
- Writes to a temp file then renames (atomic on POSIX).
- Non-fatal: exits 0 on lock contention (logs a warning but does not block
  the catch-up workflow).

Exit codes
----------
    0   Success (or skipped due to read-only / lock contention).
    1   Fatal I/O error.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent

try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass

STATE_DIR = _REPO_ROOT / "state"
TMP_DIR = _REPO_ROOT / "tmp"
HEALTH_CHECK_FILE = STATE_DIR / "health-check.md"
CONNECTOR_LOG_FILE = TMP_DIR / "connector_health_log.md"
LOCK_FILE = STATE_DIR / ".artha-lock"

_CONNECTOR_HEALTH_RE = re.compile(r"^## Connector health —")
_CATCH_UP_ENTRY_RE = re.compile(r"^## (Health Catch-up|Connector health) —")
_LOG_ROTATION_DAYS = 7


# ---------------------------------------------------------------------------
# Lock guard (non-blocking — fail-safe)
# ---------------------------------------------------------------------------

def _acquire_lock(timeout_secs: float = 3.0) -> bool:
    """Try to acquire the Artha write lock. Returns False if lock is held."""
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        if not LOCK_FILE.exists():
            try:
                LOCK_FILE.touch(exist_ok=False)
                return True
            except FileExistsError:
                pass
        time.sleep(0.2)
    return False


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)

_BOOTSTRAP_STUB_RE = re.compile(r"# Content\nsome: value")


def _read_or_init() -> str:
    """Read health-check.md or return a fresh minimal skeleton."""
    if HEALTH_CHECK_FILE.exists():
        content = HEALTH_CHECK_FILE.read_text(encoding="utf-8")
        # Detect bootstrap stub — replace with proper template
        if _BOOTSTRAP_STUB_RE.search(content):
            template = STATE_DIR / "templates" / "health-check.md"
            if template.exists():
                content = template.read_text(encoding="utf-8")
            else:
                content = "---\nschema_version: '1.1'\nlast_catch_up: never\ncatch_up_count: 0\n---\n\n## Catch-Up Run History\n\n## Connector Health\n"
        return content
    # File missing — create from template or minimal skeleton
    template = STATE_DIR / "templates" / "health-check.md"
    if template.exists():
        return template.read_text(encoding="utf-8")
    return "---\nschema_version: '1.1'\nlast_catch_up: never\ncatch_up_count: 0\n---\n\n## Catch-Up Run History\n\n## Connector Health\n"


def _update_frontmatter(content: str, updates: dict[str, object]) -> str:
    """Upsert YAML keys in the frontmatter block of *content*.

    Keys not in *updates* are preserved unchanged. If no frontmatter block
    exists, one is prepended.
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        # No frontmatter — prepend one
        fm_lines = ["---"]
        for k, v in updates.items():
            fm_lines.append(f"{k}: {_yaml_scalar(v)}")
        fm_lines.append("---")
        return "\n".join(fm_lines) + "\n" + content

    fm_body = m.group(1)
    rest = content[m.end():]

    # Parse existing key: value lines (simple YAML — no nested structures)
    existing: dict[str, str] = {}
    order: list[str] = []
    for line in fm_body.splitlines():
        kv = re.match(r"^(\S+):\s*(.*)", line)
        if kv:
            key, val = kv.group(1), kv.group(2)
            existing[key] = val
            order.append(key)

    # Apply updates
    for k, v in updates.items():
        if k not in existing:
            order.append(k)
        existing[k] = _yaml_scalar(v)

    new_fm = "---\n" + "\n".join(f"{k}: {existing[k]}" for k in order) + "\n---"
    return new_fm + rest


def _yaml_scalar(value: object) -> str:
    """Format a Python value as a YAML scalar string."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    # Quote if it contains special chars or looks like a special value
    if any(c in s for c in (':', '#', '[', ']', '{', '}', ',', '&', '*', '!', '|', '>')):
        return f'"{s}"'
    return s


# ---------------------------------------------------------------------------
# Log rotation helpers
# ---------------------------------------------------------------------------

def _rotate_connector_logs(content: str) -> str:
    """Move connector health log entries older than LOG_ROTATION_DAYS to tmp/.

    Rewrites *content* without the old entries and appends them to
    CONNECTOR_LOG_FILE (append-only archive).

    Returns the pruned content.
    """
    cutoff = time.time() - _LOG_ROTATION_DAYS * 86400
    lines = content.splitlines(keepends=True)
    kept: list[str] = []
    archived: list[str] = []
    current_block: list[str] = []
    in_connector_block = False
    block_ts: float | None = None

    for line in lines:
        if _CONNECTOR_HEALTH_RE.match(line):
            # Flush previous block
            if current_block:
                if block_ts is not None and block_ts < cutoff:
                    archived.extend(current_block)
                else:
                    kept.extend(current_block)
            current_block = [line]
            in_connector_block = True
            # Parse timestamp from header
            ts_m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC)", line)
            if ts_m:
                try:
                    block_ts = datetime.strptime(ts_m.group(1), "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc).timestamp()
                except ValueError:
                    block_ts = None
            else:
                block_ts = None
        elif in_connector_block and (line.startswith("##") or line.strip() == "---"):
            # End of the connector block
            if current_block:
                if block_ts is not None and block_ts < cutoff:
                    archived.extend(current_block)
                else:
                    kept.extend(current_block)
            current_block = []
            in_connector_block = False
            block_ts = None
            kept.append(line)
        elif in_connector_block:
            current_block.append(line)
        else:
            kept.append(line)

    # Flush any trailing block
    if current_block:
        if block_ts is not None and block_ts < cutoff:
            archived.extend(current_block)
        else:
            kept.extend(current_block)

    if archived:
        TMP_DIR.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        with CONNECTOR_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"\n\n<!-- Rotated {ts} by health_check_writer.py -->\n")
            fh.writelines(archived)

    return "".join(kept)


# ---------------------------------------------------------------------------
# Main write
# ---------------------------------------------------------------------------

def write_health_check(
    last_catch_up: str | None = None,
    email_count: int | None = None,
    domains_processed: list[str] | None = None,
    session_mode: str | None = None,
    verbose: bool = False,
) -> int:
    """Update health-check.md atomically.  Returns exit code."""
    if not _acquire_lock():
        print(
            "[health_check_writer] ⚠ Could not acquire write lock — health-check.md not updated.",
            file=sys.stderr,
        )
        return 0  # Non-fatal

    try:
        content = _read_or_init()
        content = _rotate_connector_logs(content)

        # Build frontmatter updates
        updates: dict[str, object] = {}
        if last_catch_up:
            updates["last_catch_up"] = last_catch_up
        if email_count is not None:
            updates["email_count"] = email_count
        if domains_processed is not None:
            updates["domains_processed"] = "[" + ", ".join(domains_processed) + "]"
        if session_mode:
            updates["session_mode"] = session_mode

        # Increment catch_up_count by 1
        m = _FRONTMATTER_RE.match(content)
        current_count = 0
        if m:
            cnt_m = re.search(r"^catch_up_count:\s*(\d+)", m.group(1), re.MULTILINE)
            if cnt_m:
                current_count = int(cnt_m.group(1))
        updates["catch_up_count"] = current_count + 1

        content = _update_frontmatter(content, updates)

        # Atomic write via tmp file
        TMP_DIR.mkdir(exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=STATE_DIR, prefix=".health-check-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_path, HEALTH_CHECK_FILE)
        except Exception:
            os.unlink(tmp_path)
            raise

        if verbose:
            print(f"[health_check_writer] ✓ Updated {HEALTH_CHECK_FILE}", file=sys.stderr)
            if updates:
                for k, v in updates.items():
                    print(f"  {k}: {v}", file=sys.stderr)
        else:
            print("[health_check_writer] ✓ health-check.md updated", file=sys.stderr)
        return 0

    except Exception as exc:
        print(f"[health_check_writer] ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        _release_lock()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="health_check_writer.py",
        description="Atomically update state/health-check.md after a catch-up session.",
    )
    p.add_argument(
        "--last-catch-up",
        metavar="ISO8601",
        help="Catch-up completion timestamp (default: now)",
    )
    p.add_argument(
        "--email-count",
        type=int,
        metavar="N",
        help="Number of emails processed this session",
    )
    p.add_argument(
        "--domains-processed",
        metavar="LIST",
        help="Comma-separated list of domains processed (e.g. finance,kids)",
    )
    p.add_argument(
        "--mode",
        dest="session_mode",
        choices=["normal", "degraded", "offline", "read-only"],
        help="Session mode",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    last_catch_up = args.last_catch_up or datetime.now(timezone.utc).isoformat()
    domains = [d.strip() for d in args.domains_processed.split(",")] if args.domains_processed else None

    return write_health_check(
        last_catch_up=last_catch_up,
        email_count=args.email_count,
        domains_processed=domains,
        session_mode=args.session_mode,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
