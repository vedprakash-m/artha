#!/usr/bin/env python3
"""
scripts/calendar_writer.py — Consume pipeline GCal/Outlook output and persist
to state/calendar.md in a structured event schema.

The pipeline already fetches calendar events from Google Calendar and
Microsoft Graph.  This script reads the pipeline JSONL output from
tmp/pipeline_output.jsonl (or stdin) and appends new events to
state/calendar.md, deduplicating on (date, title) to prevent repeated
appends across multiple catch-up runs.

Usage
-----
    # From pipeline stdout:
    python scripts/pipeline.py | python scripts/calendar_writer.py

    # From saved pipeline output:
    python scripts/calendar_writer.py --input tmp/pipeline_output.jsonl

    # Dry-run (show what would be written):
    python scripts/calendar_writer.py --dry-run

Exit codes
----------
    0   Success (events written or nothing new to write).
    1   Fatal I/O error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
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
CALENDAR_FILE = STATE_DIR / "calendar.md"

# Calendar event source tags from pipeline connectors
_CALENDAR_SOURCE_TAGS = frozenset({
    "google_calendar",
    "gcal",
    "outlook_calendar",
    "msgraph_calendar",
    "caldav_calendar",
    "workiq_calendar",
})

_BOOTSTRAP_STUB_RE = re.compile(r"# Content\nsome: value")


def _is_calendar_record(record: dict) -> bool:
    """Return True if this pipeline record is a calendar event."""
    source = record.get("source", record.get("source_tag", ""))
    rtype = record.get("type", record.get("record_type", ""))
    # Accept records tagged as calendar sources OR with type=event/calendar
    return (
        any(tag in source for tag in _CALENDAR_SOURCE_TAGS)
        or rtype in ("event", "calendar_event", "calendar")
    )


def _event_dedup_key(event: dict) -> str:
    """Stable dedup key based on date + title."""
    date = event.get("date", event.get("start", event.get("start_date", "unknown")))
    title = event.get("title", event.get("summary", event.get("subject", "untitled")))
    return hashlib.sha256(f"{date}:{title}".encode()).hexdigest()[:16]


def _existing_dedup_keys(content: str) -> set[str]:
    """Extract all dedup keys already embedded in calendar.md."""
    return set(re.findall(r"<!-- dedup:([a-f0-9]{16}) -->", content))


def _format_event(event: dict, dedup_key: str) -> str:
    """Format a pipeline calendar record as a Markdown list entry."""
    date = event.get("date", event.get("start", event.get("start_date", "unknown")))
    end = event.get("end", event.get("end_date", ""))
    title = event.get("title", event.get("summary", event.get("subject", "Untitled Event")))
    location = event.get("location", "")
    description = event.get("description", event.get("body", ""))
    source = event.get("source", event.get("source_tag", ""))

    lines = [f"\n- **{title}**  <!-- dedup:{dedup_key} -->"]
    if date:
        date_str = f"  - Date: {date}"
        if end and end != date:
            date_str += f" → {end}"
        lines.append(date_str)
    if location:
        lines.append(f"  - Location: {location}")
    if description:
        desc_short = description.strip()[:200]
        lines.append(f"  - Notes: {desc_short}")
    if source:
        lines.append(f"  - Source: {source}")

    return "\n".join(lines) + "\n"


def _read_or_init_calendar() -> str:
    """Read calendar.md or return a fresh skeleton."""
    if CALENDAR_FILE.exists():
        content = CALENDAR_FILE.read_text(encoding="utf-8")
        # Detect bootstrap stub — replace with proper schema
        if _BOOTSTRAP_STUB_RE.search(content):
            content = _fresh_skeleton()
        return content
    template = STATE_DIR / "templates" / "calendar.md"
    if template.exists():
        tmpl = template.read_text(encoding="utf-8")
        if not _BOOTSTRAP_STUB_RE.search(tmpl):
            return tmpl
    return _fresh_skeleton()


def _fresh_skeleton() -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"---\ndomain: calendar\nlast_updated: \"{today}\"\nschema_version: \"1.0\"\n---\n\n"
        "# Calendar\n\n"
        "## Upcoming Events\n\n"
        "<!-- Events are appended here by calendar_writer.py -->\n\n"
        "## Past Events\n"
    )


def _update_last_updated(content: str, timestamp: str) -> str:
    return re.sub(
        r'(last_updated:\s*)["\']?[^"\'\n]*["\']?',
        f'last_updated: "{timestamp}"',
        content,
        count=1,
    )


def run(
    input_path: Path | None = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Main logic. Returns exit code."""
    # Read pipeline records
    records: list[dict] = []
    if input_path and input_path.exists():
        with input_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    elif not sys.stdin.isatty():
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    else:
        # Also try default tmp location
        default_input = TMP_DIR / "pipeline_output.jsonl"
        if default_input.exists():
            with default_input.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    if not records:
        print("[calendar_writer] No pipeline records to process.", file=sys.stderr)
        return 0

    # Filter calendar events
    events = [r for r in records if _is_calendar_record(r)]
    print(f"[calendar_writer] {len(records)} total records, {len(events)} calendar events.", file=sys.stderr)

    if not events:
        print("[calendar_writer] No calendar events in pipeline output.", file=sys.stderr)
        return 0

    content = _read_or_init_calendar()
    existing_keys = _existing_dedup_keys(content)

    new_entries: list[str] = []
    skipped = 0
    for event in events:
        key = _event_dedup_key(event)
        if key in existing_keys:
            skipped += 1
            if verbose:
                title = event.get("title", event.get("summary", "?"))
                print(f"  [skip-dup] {title}", file=sys.stderr)
            continue
        entry = _format_event(event, key)
        new_entries.append(entry)
        existing_keys.add(key)
        if verbose or dry_run:
            title = event.get("title", event.get("summary", "?"))
            print(f"  [{'dry-run' if dry_run else 'add'}] {title}")

    print(
        f"[calendar_writer] {len(new_entries)} new events to write, {skipped} duplicates skipped.",
        file=sys.stderr,
    )

    if not new_entries:
        return 0

    if dry_run:
        print("[calendar_writer] Dry-run: no write performed.", file=sys.stderr)
        return 0

    # Insert after "## Upcoming Events" or before "## Past Events"
    upcoming_marker = "\n## Upcoming Events\n"
    past_marker = "\n## Past Events"
    comment_marker = "\n<!-- Events are appended here by calendar_writer.py -->\n"

    insert_block = "".join(new_entries)

    if comment_marker in content:
        content = content.replace(comment_marker, comment_marker + insert_block)
    elif upcoming_marker in content:
        idx = content.index(upcoming_marker) + len(upcoming_marker)
        content = content[:idx] + "\n" + insert_block + content[idx:]
    elif past_marker in content:
        idx = content.index(past_marker)
        content = content[:idx] + insert_block + content[idx:]
    else:
        content += "\n## Upcoming Events\n\n" + insert_block

    today_iso = datetime.now(timezone.utc).isoformat()
    content = _update_last_updated(content, today_iso)

    try:
        CALENDAR_FILE.write_text(content, encoding="utf-8")
    except OSError as exc:
        print(f"[calendar_writer] ERROR writing {CALENDAR_FILE}: {exc}", file=sys.stderr)
        return 1

    print(f"[calendar_writer] ✓ Wrote {len(new_entries)} events to {CALENDAR_FILE}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="calendar_writer.py",
        description="Persist pipeline calendar events to state/calendar.md",
    )
    p.add_argument(
        "--input", "-i",
        metavar="JSONL",
        help="Path to pipeline JSONL file (default: reads stdin or tmp/pipeline_output.jsonl)",
    )
    p.add_argument("--dry-run", action="store_true", help="Show what would be written")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    input_path = Path(args.input) if args.input else None
    return run(input_path=input_path, dry_run=args.dry_run, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
