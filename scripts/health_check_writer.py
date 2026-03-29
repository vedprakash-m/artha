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
CONFIG_DIR = _REPO_ROOT / "config"
HEALTH_CHECK_FILE = STATE_DIR / "health-check.md"
CATCH_UP_RUNS_FILE = STATE_DIR / "catch_up_runs.yaml"
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
# Catch-up run history (state/catch_up_runs.yaml)
# ---------------------------------------------------------------------------

def _append_catch_up_run(
    timestamp: str,
    engagement_rate: float | None = None,
    user_ois: int | None = None,
    system_ois: int | None = None,
    items_surfaced: int | None = None,
    correction_count: int | None = None,
    briefing_format: str | None = None,
    email_count: int | None = None,
    domains_processed: list[str] | None = None,
) -> None:
    """Append a structured catch-up run entry to state/catch_up_runs.yaml.

    This is a NEW function distinct from _update_frontmatter() — it writes
    a growing YAML list rather than scalar key-value frontmatter pairs.
    Uses the same atomic (tempfile + os.replace) pattern as health-check.md.

    Retention: keeps the last 100 entries (~77 days at 1.3 catch-ups/day).

    Field naming: uses 'engagement_rate' (float scalar) NOT 'signal_noise'
    (which is a compound object in Artha.core.md Step 16 schema). R2 reads
    'engagement_rate' with 'signal_noise' as legacy fallback.
    """
    try:
        import yaml
    except ImportError:
        return  # PyYAML not available; skip silently

    # Build entry dict — include only fields that were actually provided
    entry: dict = {"timestamp": timestamp}
    # engagement_rate: store null if items_surfaced == 0 (no alerts = no-signal run)
    if items_surfaced is not None and items_surfaced == 0:
        entry["engagement_rate"] = None  # YAML null — R2 skips null entries
    elif engagement_rate is not None:
        entry["engagement_rate"] = round(float(engagement_rate), 4)
    else:
        entry["engagement_rate"] = None

    if user_ois is not None:
        entry["user_ois"] = int(user_ois)
    if system_ois is not None:
        entry["system_ois"] = int(system_ois)
    if items_surfaced is not None:
        entry["items_surfaced"] = int(items_surfaced)
    if correction_count is not None:
        entry["correction_count"] = int(correction_count)
    if briefing_format:
        entry["briefing_format"] = briefing_format
    if email_count is not None:
        entry["email_count"] = int(email_count)
    if domains_processed:
        entry["domains_processed"] = list(domains_processed)

    # Read existing runs list
    existing: list[dict] = []
    if CATCH_UP_RUNS_FILE.exists():
        try:
            raw = yaml.safe_load(CATCH_UP_RUNS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = raw
        except Exception:
            existing = []

    # Append and apply retention limit (last 100 entries)
    existing.append(entry)
    if len(existing) > 100:
        existing = existing[-100:]

    # Atomic write via tempfile + os.replace
    STATE_DIR.mkdir(exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=STATE_DIR, prefix=".catch_up_runs-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(
                "# state/catch_up_runs.yaml\n"
                "# Machine-parseable append-only run history.\n"
                "# Written by health_check_writer.py; read by briefing_adapter.py.\n"
                "# Field: engagement_rate (float) — NOT 'signal_noise' (compound object).\n"
                "---\n"
            )
            yaml.dump(existing, fh, allow_unicode=True, default_flow_style=False)
        os.replace(tmp_path, CATCH_UP_RUNS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Skill config helpers (R7 user-response writes)
# ---------------------------------------------------------------------------

def _update_skill_in_config(skill_name: str, key: str, value: str, comment: str = "") -> bool:
    """Update a key in the given skill's block within config/skills.yaml.

    Uses line-by-line parsing to preserve comments and formatting.
    Returns True on success, False if skill not found.
    """
    config_path = CONFIG_DIR / "skills.yaml"
    if not config_path.exists():
        return False

    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    in_target = False
    result: list[str] = []
    modified = False

    for line in lines:
        # Detect this skill's block start (2-space indent + name + colon)
        if re.match(rf'^  {re.escape(skill_name)}:', line):
            in_target = True
        elif re.match(r'^  \w', line) and in_target:
            # Next 2-space-indented key — exited target block
            in_target = False

        if in_target and re.match(rf'^    {re.escape(key)}:', line):
            suffix = f"  # {comment}" if comment else ""
            line = f"    {key}: {value}{suffix}\n"
            modified = True

        result.append(line)

    if modified:
        config_path.write_text("".join(result), encoding="utf-8")
    return modified


def disable_skill(skill_name: str) -> bool:
    """Set enabled: false for a skill (R7 user-approved disable).

    Adds comment '# disabled by R7 — re-enable when needed' for traceability.
    Logged to state/audit.md per P6 earned-autonomy protocol.
    """
    ok = _update_skill_in_config(
        skill_name, "enabled", "false",
        comment="disabled by R7 — re-enable when needed"
    )
    if ok:
        # Append to audit log
        try:
            audit_path = STATE_DIR / "audit.md"
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            audit_line = f"\n{ts} | skill_disabled | skill: {skill_name} | reason: R7 (user approved)\n"
            with audit_path.open("a", encoding="utf-8") as fh:
                fh.write(audit_line)
        except Exception:
            pass
    return ok


def suppress_skill_prompt(skill_name: str) -> bool:
    """Set suppress_zero_prompt: true for a skill (R7 user 'keep running').

    Prevents further R7 disable prompts for this skill.
    """
    # First check if key exists; if not, append it after the 'enabled:' line
    config_path = CONFIG_DIR / "skills.yaml"
    if not config_path.exists():
        return False

    ok = _update_skill_in_config(skill_name, "suppress_zero_prompt", "true")
    if not ok:
        # Key doesn't exist yet — inject after 'enabled:' line in skill block
        lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
        in_target = False
        result: list[str] = []
        for line in lines:
            if re.match(rf'^  {re.escape(skill_name)}:', line):
                in_target = True
            elif re.match(r'^  \w', line) and in_target:
                in_target = False
            if in_target and re.match(r'^    enabled:', line):
                result.append(line)
                result.append("    suppress_zero_prompt: true\n")
                continue
            result.append(line)
        config_path.write_text("".join(result), encoding="utf-8")
        ok = True
    return ok


# ---------------------------------------------------------------------------
# Main write
# ---------------------------------------------------------------------------

def write_health_check(
    last_catch_up: str | None = None,
    email_count: int | None = None,
    domains_processed: list[str] | None = None,
    session_mode: str | None = None,
    briefing_format: str | None = None,
    engagement_rate: float | None = None,
    user_ois: int | None = None,
    system_ois: int | None = None,
    items_surfaced: int | None = None,
    correction_count: int | None = None,
    verbose: bool = False,
) -> int:
    """Update health-check.md atomically and append to catch_up_runs.yaml.  Returns exit code."""
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

        # Append structured run entry to state/catch_up_runs.yaml
        # (non-fatal — health-check.md update already succeeded above)
        try:
            _append_catch_up_run(
                timestamp=last_catch_up or datetime.now(timezone.utc).isoformat(),
                engagement_rate=engagement_rate,
                user_ois=user_ois,
                system_ois=system_ois,
                items_surfaced=items_surfaced,
                correction_count=correction_count,
                briefing_format=briefing_format,
                email_count=email_count,
                domains_processed=domains_processed,
            )
            if verbose:
                print("[health_check_writer] ✓ catch_up_runs.yaml appended", file=sys.stderr)
        except Exception as run_exc:
            print(f"[health_check_writer] ⚠ catch_up_runs.yaml append failed: {run_exc}", file=sys.stderr)

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
        help="Session mode (system health: normal|degraded|offline|read-only)",
    )
    p.add_argument(
        "--briefing-format",
        dest="briefing_format",
        choices=["standard", "flash", "deep", "digest"],
        help="Briefing format used this session (standard|flash|deep|digest)",
    )
    p.add_argument(
        "--engagement-rate",
        dest="engagement_rate",
        type=float,
        metavar="RATE",
        help="Engagement rate: (user_ois + corrections) / items_surfaced (0.0–1.0)",
    )
    p.add_argument(
        "--user-ois",
        dest="user_ois",
        type=int,
        metavar="N",
        help="OIs explicitly created by the user this session (origin: user)",
    )
    p.add_argument(
        "--system-ois",
        dest="system_ois",
        type=int,
        metavar="N",
        help="OIs auto-extracted by the pipeline (origin: system)",
    )
    p.add_argument(
        "--items-surfaced",
        dest="items_surfaced",
        type=int,
        metavar="N",
        help="Count of P0/P1/P2 alerts surfaced during Steps 7–8",
    )
    p.add_argument(
        "--correction-count",
        dest="correction_count",
        type=int,
        metavar="N",
        help="Number of user corrections applied during Step 19 calibration",
    )
    p.add_argument(
        "--disable-skill",
        dest="disable_skill",
        metavar="SKILL_NAME",
        help="Disable a skill in config/skills.yaml (R7 user-approved disable)",
    )
    p.add_argument(
        "--suppress-skill-prompt",
        dest="suppress_skill_prompt",
        metavar="SKILL_NAME",
        help="Set suppress_zero_prompt: true for a skill (R7 user 'keep running')",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Handle R7 skill config writes first (independent of health-check update)
    if args.disable_skill:
        ok = disable_skill(args.disable_skill)
        msg = "disabled" if ok else "not found (check skill name)"
        print(f"[health_check_writer] --disable-skill {args.disable_skill}: {msg}", file=sys.stderr)
        if not ok:
            return 1

    if args.suppress_skill_prompt:
        ok = suppress_skill_prompt(args.suppress_skill_prompt)
        msg = "suppress_zero_prompt set" if ok else "not found (check skill name)"
        print(f"[health_check_writer] --suppress-skill-prompt {args.suppress_skill_prompt}: {msg}", file=sys.stderr)

    # If only skill config flags were passed, exit without health-check update
    if args.disable_skill and not any([
        args.last_catch_up, args.email_count, args.domains_processed,
        args.session_mode, args.briefing_format, args.engagement_rate,
        args.user_ois, args.system_ois, args.items_surfaced, args.correction_count,
    ]):
        return 0

    last_catch_up = args.last_catch_up or datetime.now(timezone.utc).isoformat()
    domains = [d.strip() for d in args.domains_processed.split(",")] if args.domains_processed else None

    return write_health_check(
        last_catch_up=last_catch_up,
        email_count=args.email_count,
        domains_processed=domains,
        session_mode=args.session_mode,
        briefing_format=args.briefing_format,
        engagement_rate=args.engagement_rate,
        user_ois=args.user_ois,
        system_ois=args.system_ois,
        items_surfaced=args.items_surfaced,
        correction_count=args.correction_count,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
