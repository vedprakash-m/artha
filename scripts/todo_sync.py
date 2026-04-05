#!/usr/bin/env python3
"""
todo_sync.py — Artha: Sync open_items.md ↔ Microsoft To Do
===========================================================
Bidirectional sync between Artha's persistent action tracker and Microsoft To Do.

Modes:
  --push   (default)  Push items with status=open and todo_id="" → To Do
  --pull              Pull completion status from To Do → update open_items.md
  --dry-run           Print what would be pushed/pulled without calling API
  --status            Print sync summary (no API calls)

Called by catch-up workflow:
  • Step 0b: todo_sync.py --pull  (marks completed items before briefing)
  • Step 8f: todo_sync.py --push  (pushes new open items to To Do)

Data flow:
  open_items.md → [parse] → [POST /me/todo/lists/{listId}/tasks] → write todo_id back
  open_items.md ← [update status: done] ← [GET task status] ← [iterate todo_ids]

Failure mode: non-blocking. If To Do API fails, log warning and continue.
  Unpushed items have todo_id: "" → will be retried on next catch-up.

Ref: T-1B.6.3, T-1B.6.4, TS §7.1 Steps 0b + 8f
"""

from __future__ import annotations

import sys
# Ensure we run inside the Artha venv. Ref: standardization.md §7.3
from _bootstrap import reexec_in_venv; reexec_in_venv()

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from lib.retry import with_retry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from lib.common import ARTHA_DIR as _ARTHA_DIR_PATH

ARTHA_DIR       = str(_ARTHA_DIR_PATH)
OPEN_ITEMS_FILE = os.path.join(ARTHA_DIR, "state", "open_items.md")
CONFIG_FILE    = os.path.join(ARTHA_DIR, "config", "artha_config.yaml")
AUDIT_FILE     = os.path.join(ARTHA_DIR, "state", "audit.md")

GRAPH_TODO_BASE = "https://graph.microsoft.com/v1.0/me/todo/lists"

# Priority mapping: Artha → Graph API
_IMPORTANCE_MAP = {
    "P0": "high",
    "P1": "normal",
    "P2": "low",
}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _graph_request(
    url: str,
    method: str = "GET",
    body: Optional[dict] = None,
    access_token: str = "",
) -> dict:
    """Make an authenticated Graph API request. Returns parsed JSON dict."""
    data    = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


# ---------------------------------------------------------------------------
# open_items.md parser / writer
# ---------------------------------------------------------------------------

def _parse_open_items(content: str) -> list[dict]:
    """
    Parse YAML-ish list items from open_items.md.
    Each item block starts with `- id:` and ends at the next `- id:` or section break.
    Returns list of dicts with all fields.
    """
    items = []
    # Find all item blocks — a block starts with `- id:` and ends before the next `- id:`
    # or at a heading (## ...) or end of file.
    block_pattern = re.compile(
        r"^- id:\s*(.+?)$(.+?)(?=^- id:|\Z|^##)",
        re.MULTILINE | re.DOTALL,
    )
    for m in block_pattern.finditer(content):
        item_id = m.group(1).strip()
        block   = m.group(2)
        item: dict = {"id": item_id}

        for field in ("date_added", "source_domain", "description", "deadline",
                      "priority", "status", "todo_id"):
            fm = re.search(rf"^\s+{field}:\s*(.+)$", block, re.MULTILINE)
            if fm:
                val = fm.group(1).strip()
                # Strip YAML quotes
                if (val.startswith('"') and val.endswith('"')) or \
                   (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                item[field] = val
            else:
                item[field] = ""
        items.append(item)
    return items


def _write_todo_id(item_id: str, todo_id: str) -> None:
    """Update the todo_id field for a specific item in open_items.md."""
    with open(OPEN_ITEMS_FILE, encoding='utf-8') as f:
        content = f.read()

    # Find the id: line and the todo_id line within that item's block
    # Pattern: after `- id: {item_id}`, find `  todo_id: ...` in the same block
    pattern = re.compile(
        rf"(- id:\s*{re.escape(item_id)}\b.+?  todo_id:\s*)(\S*)",
        re.DOTALL,
    )
    updated = pattern.sub(rf'\g<1>{todo_id}', content, count=1)

    if updated == content:
        print(f"[todo_sync] ⚠ Could not update todo_id for {item_id}", file=sys.stderr)
        return

    with open(OPEN_ITEMS_FILE, "w", encoding='utf-8') as f:
        f.write(updated)


def _write_item_status(item_id: str, new_status: str, date_resolved: str) -> None:
    """Update the status field and (optionally) date_resolved for an item."""
    with open(OPEN_ITEMS_FILE, encoding='utf-8') as f:
        content = f.read()

    # Update status
    pattern = re.compile(
        rf"(- id:\s*{re.escape(item_id)}\b.+?  status:\s*)(\S+)",
        re.DOTALL,
    )
    updated = pattern.sub(rf'\g<1>{new_status}', content, count=1)

    # Add date_resolved: if status is now done (insert after deadline: line)
    if new_status == "done" and "date_resolved:" not in updated:
        # Insert `  date_resolved: YYYY-MM-DD` after the `  status: done` line
        updated = re.sub(
            rf"(- id:\s*{re.escape(item_id)}\b.+?  status:\s*done\n)",
            rf"\g<1>    date_resolved: {date_resolved}\n",
            updated,
            count=1,
            flags=re.DOTALL,
        )

    with open(OPEN_ITEMS_FILE, "w", encoding='utf-8') as f:
        f.write(updated)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_list_ids() -> dict[str, str]:
    """Load todo_lists: mapping from user_profile.yaml (preferred) or artha_config.yaml (legacy)."""
    # Preferred: read from user_profile.yaml via profile_loader
    try:
        from profile_loader import get as _profile_get
        todo_lists = _profile_get("integrations.microsoft_graph.todo_lists", {})
        if isinstance(todo_lists, dict) and todo_lists:
            return todo_lists
    except Exception:
        pass  # profile_loader may not be available — fall through

    # Legacy fallback: parse artha_config.yaml
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, encoding='utf-8') as f:
        content = f.read()
    m = re.search(r"^todo_lists:\n((?:  [^\n]*\n)*)", content, re.MULTILINE)
    if not m:
        return {}
    ids: dict[str, str] = {}
    for line in m.group(1).splitlines():
        parts = line.strip().split(":", 1)
        if len(parts) == 2:
            key = parts[0].strip()
            val = parts[1].strip().strip('"\'')
            if key and val:
                ids[key] = val
    return ids


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _audit_log(action: str, detail: str) -> None:
    """Append a line to state/audit.md."""
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = f"[{ts}] TODO_SYNC | action: {action} | {detail}\n"
    try:
        with open(AUDIT_FILE, "a") as f:
            f.write(entry)
    except OSError:
        pass  # audit is best-effort


# ---------------------------------------------------------------------------
# Push: open_items → Microsoft To Do
# ---------------------------------------------------------------------------

def push_items(access_token: str, dry_run: bool = False) -> dict:
    """
    Push open items with todo_id=="" to Microsoft To Do.
    Returns {"pushed": N, "failed": N, "skipped": N}.
    """
    if not os.path.exists(OPEN_ITEMS_FILE):
        print("[todo_sync] ⚠ open_items.md not found — nothing to push", file=sys.stderr)
        return {"pushed": 0, "failed": 0, "skipped": 0}

    with open(OPEN_ITEMS_FILE, encoding='utf-8') as f:
        content = f.read()

    items   = _parse_open_items(content)
    pending = [i for i in items if i.get("status") == "open" and not i.get("todo_id")]

    if not pending:
        print("[todo_sync] No new items to push (all open items already have todo_id)")
        return {"pushed": 0, "failed": 0, "skipped": len(items)}

    list_ids = _load_list_ids()
    if not list_ids and not dry_run:
        print("[todo_sync] ⚠ No todo list IDs in artha_config.yaml", file=sys.stderr)
        print("  Run: python scripts/setup_todo_lists.py", file=sys.stderr)
        return {"pushed": 0, "failed": len(pending), "skipped": 0}

    pushed = failed = 0
    for item in pending:
        domain   = item.get("source_domain", "general").lower()
        list_id  = list_ids.get(domain, list_ids.get("general", ""))
        title    = item.get("description", "(no description)")
        deadline = item.get("deadline", "")
        priority = item.get("priority", "P1")
        item_id  = item["id"]

        if dry_run:
            print(f"  [DRY RUN] Would push {item_id}: '{title[:60]}' → {domain}")
            continue

        if not list_id:
            print(f"[todo_sync] ⚠ No list_id for domain '{domain}' — skipping {item_id}",
                  file=sys.stderr)
            failed += 1
            continue

        # Build task body
        task_body: dict = {
            "title":      title,
            "importance": _IMPORTANCE_MAP.get(priority, "normal"),
        }
        if deadline:
            try:
                # Graph API wants dueDateTime.dateTime in UTC, e.g. "2026-03-13T00:00:00.0000000"
                task_body["dueDateTime"] = {
                    "dateTime": f"{deadline}T00:00:00.0000000",
                    "timeZone": "America/Los_Angeles",
                }
            except Exception:
                pass  # Skip bad deadline

        try:
            result = with_retry(
                lambda: _graph_request(
                    f"{GRAPH_TODO_BASE}/{list_id}/tasks",
                    method="POST",
                    body=task_body,
                    access_token=access_token,
                ),
                context=f"tasks.create({item_id})",
                label="todo_sync",
            )
            todo_id = result.get("id", "")
            if todo_id:
                _write_todo_id(item_id, todo_id)
                _audit_log("push", f"item_id={item_id} todo_id={todo_id[:16]}...")
                print(f"[todo_sync] ✓ Pushed {item_id}: '{title[:50]}' → {domain}")
                pushed += 1
            else:
                print(f"[todo_sync] ⚠ No task ID returned for {item_id}", file=sys.stderr)
                failed += 1
        except Exception as exc:
            print(f"[todo_sync] ✗ Failed to push {item_id}: {exc}", file=sys.stderr)
            _audit_log("push_failed", f"item_id={item_id} error={exc}")
            failed += 1

    return {"pushed": pushed, "failed": failed, "skipped": len(items) - len(pending)}


# ---------------------------------------------------------------------------
# Pull: Microsoft To Do completion → open_items.md
# ---------------------------------------------------------------------------

def pull_completions(access_token: str, dry_run: bool = False) -> dict:
    """
    Pull completion status from To Do for all items with a known todo_id.
    Marks completed items as status: done in open_items.md.
    Returns {"completed": N, "still_open": N, "failed": N}.
    """
    if not os.path.exists(OPEN_ITEMS_FILE):
        return {"completed": 0, "still_open": 0, "failed": 0}

    with open(OPEN_ITEMS_FILE, encoding='utf-8') as f:
        content = f.read()

    items       = _parse_open_items(content)
    tracked     = [i for i in items if i.get("todo_id") and i.get("status") == "open"]
    list_ids    = _load_list_ids()

    completed = failed = still_open = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for item in tracked:
        domain   = item.get("source_domain", "general").lower()
        list_id  = list_ids.get(domain, list_ids.get("general", ""))
        todo_id  = item["todo_id"]
        item_id  = item["id"]

        if not list_id:
            failed += 1
            continue

        try:
            result = with_retry(
                lambda: _graph_request(
                    f"{GRAPH_TODO_BASE}/{list_id}/tasks/{todo_id}",
                    access_token=access_token,
                ),
                context=f"tasks.get({item_id})",
                label="todo_sync",
            )
            status = result.get("status", "notStarted")

            if status == "completed":
                if dry_run:
                    print(f"  [DRY RUN] Would mark {item_id} as done")
                else:
                    _write_item_status(item_id, "done", today)
                    _audit_log("pull_completed", f"item_id={item_id} todo_id={todo_id[:16]}...")
                    print(f"[todo_sync] ✓ Marked {item_id} as done (completed in To Do)")
                completed += 1
            else:
                still_open += 1
        except Exception as exc:
            print(f"[todo_sync] ⚠ Could not check {item_id}: {exc}", file=sys.stderr)
            failed += 1

    return {"completed": completed, "still_open": still_open, "failed": failed}


# ---------------------------------------------------------------------------
# Status summary
# ---------------------------------------------------------------------------

def print_status() -> None:
    """Print sync summary without calling any API."""
    if not os.path.exists(OPEN_ITEMS_FILE):
        print("[todo_sync] open_items.md not found")
        return

    with open(OPEN_ITEMS_FILE, encoding='utf-8') as f:
        content = f.read()

    items = _parse_open_items(content)

    open_with_id    = [i for i in items if i.get("status") == "open" and i.get("todo_id")]
    open_without_id = [i for i in items if i.get("status") == "open" and not i.get("todo_id")]
    in_progress     = [i for i in items if i.get("status") == "in-progress"]
    done            = [i for i in items if i.get("status") == "done"]

    list_ids = _load_list_ids()

    print(f"Open Items Sync Status")
    print(f"─" * 40)
    print(f"  Open (synced to To Do):   {len(open_with_id)}")
    print(f"  Open (pending push):      {len(open_without_id)}")
    print(f"  In-progress:              {len(in_progress)}")
    print(f"  Done / Resolved:          {len(done)}")
    print(f"  To Do list IDs loaded:    {'✓' if list_ids else '✗ Run setup_todo_lists.py'}")

    if open_without_id:
        print(f"\n  Items pending push ({len(open_without_id)}):")
        for item in open_without_id[:5]:
            print(f"    • {item['id']}: {item.get('description','')[:60]}")
        if len(open_without_id) > 5:
            print(f"    ... and {len(open_without_id) - 5} more")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync Artha open_items.md ↔ Microsoft To Do"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--push", action="store_true", default=True,
                      help="Push new open items to To Do (default)")
    mode.add_argument("--pull", action="store_true",
                      help="Pull completion status from To Do")
    mode.add_argument("--status", action="store_true",
                      help="Print sync summary (no API calls)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without calling API")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    if args.dry_run:
        # Determine mode for dry-run
        mode_name = "pull" if args.pull else "push"
        print(f"[todo_sync] DRY RUN ({mode_name} mode)")

    # Load Graph token
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    try:
        from setup_msgraph_oauth import ensure_valid_token
    except ImportError:
        print("ERROR: setup_msgraph_oauth.py not found", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run:
        try:
            token_data   = ensure_valid_token()
            access_token = token_data["access_token"]
        except Exception as exc:
            print(f"[todo_sync] ERROR: Could not get Graph token: {exc}", file=sys.stderr)
            print("[todo_sync] Run: python scripts/setup_msgraph_oauth.py", file=sys.stderr)
            sys.exit(1)
    else:
        access_token = ""  # not used in dry-run

    if args.pull:
        result = pull_completions(access_token, dry_run=args.dry_run)
        print(f"[todo_sync] Pull complete: {result['completed']} completed, "
              f"{result['still_open']} still open, {result['failed']} failed")
    else:
        result = push_items(access_token, dry_run=args.dry_run)
        if not args.dry_run:
            print(f"[todo_sync] Push complete: {result['pushed']} pushed, "
                  f"{result['failed']} failed, {result['skipped']} already synced")


if __name__ == "__main__":
    main()
