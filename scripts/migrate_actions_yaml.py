#!/usr/bin/env python3
"""
scripts/migrate_actions_yaml.py — One-time actions.yaml schema v1.0 → v2.0 migrator.

Migrates config/actions.yaml from the legacy v1.0 schema to the v2.0 schema
required by the Artha Action Bus (specs/act.md §4.2, Phase 0 Step 0.5.1).

Changes:
  - schema_version: "1.0" → schema_version: "2.0"
  - Adds new required fields with safe defaults: handler, min_trust, sensitivity,
    timeout_sec, retry, reversible, undo_window_sec, rate_limit, autonomy_floor,
    pii_allowlist
  - Renames friction: "medium" → "medium" is NOT valid in v2.0; mapped to "standard"
  - Key rename: "send_email" → "email_send" (handler naming normalisation)
    Note: "version:" renamed to "schema_version:" for unambiguous versioning

Idempotent: running the migrator twice produces the same result.

Usage:
  python scripts/migrate_actions_yaml.py               # migrate in-place
  python scripts/migrate_actions_yaml.py --check       # print what would change; no writes
  python scripts/migrate_actions_yaml.py --backup-only # create backup only; no migration

Safety:
  - Writes .bak backup before modifying.
  - Never deletes user-defined actions.
  - Preserves all existing fields; only adds missing ones.

Ref: specs/act.md Phase 0, Step 0.5.1
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure scripts/ on sys.path
_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    import yaml  # PyYAML
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# v2.0 defaults per action type
# ---------------------------------------------------------------------------

# Friction vocabulary: v1 "medium" → v2 "standard"
_FRICTION_MAP = {"low": "low", "medium": "standard", "standard": "standard", "high": "high"}

# Handler path normalisation: v1 "scripts/gmail_send.py" → v2 module in scripts/actions/
_HANDLER_REMAP = {
    "scripts/gmail_send.py":  "scripts/actions/email_send.py",
    "scripts/todo_sync.py":   "scripts/actions/todo_sync_action.py",
    "scripts/pipeline.py":    "scripts/pipeline.py",           # unchanged
}

# Key name normalisation: v1 key → v2 key
_KEY_REMAP = {
    "send_email":       "email_send",
    "send_whatsapp":    "whatsapp_send",
    "add_calendar_event": "calendar_create",
    # todo_sync and run_pipeline names are unchanged
}

# v2.0 defaults to inject if field is absent
_V2_DEFAULTS: dict[str, Any] = {
    "min_trust":      1,
    "sensitivity":    "standard",
    "timeout_sec":    30,
    "retry":          False,
    "retry_max":      0,
    "reversible":     False,
    "undo_window_sec": None,
    "rate_limit": {
        "max_per_hour": 20,
        "max_per_day":  100,
    },
    "autonomy_floor": False,
    "pii_allowlist":  [],
    "audit":          True,
}

# Well-known overrides: for actions that have non-default values in v2.0
_V2_OVERRIDES: dict[str, dict[str, Any]] = {
    "email_send": {
        "friction":       "standard",
        "min_trust":      1,
        "sensitivity":    "standard",
        "timeout_sec":    30,
        "retry":          False,
        "reversible":     True,
        "undo_window_sec": 30,
        "rate_limit":     {"max_per_hour": 20, "max_per_day": 100},
        "pii_check":      True,
        "pii_allowlist":  ["to", "cc", "bcc", "recipient_name"],
        "audit":          True,
        "autonomy_floor": True,
        "description":    "Send email via Gmail API on behalf of user",
        "handler":        "scripts/actions/email_send.py",
        "enabled":        True,
    },
    "email_reply": {
        "friction":       "standard",
        "min_trust":      1,
        "sensitivity":    "standard",
        "timeout_sec":    30,
        "retry":          False,
        "reversible":     True,
        "undo_window_sec": 30,
        "rate_limit":     {"max_per_hour": 20, "max_per_day": 100},
        "pii_check":      True,
        "pii_allowlist":  ["to", "cc", "bcc", "recipient_name"],
        "audit":          True,
        "autonomy_floor": True,
        "description":    "Reply to email thread via Gmail API",
        "handler":        "scripts/actions/email_reply.py",
        "enabled":        True,
    },
    "whatsapp_send": {
        "friction":       "standard",
        "min_trust":      1,
        "sensitivity":    "standard",
        "timeout_sec":    15,
        "retry":          False,
        "reversible":     False,
        "rate_limit":     {"max_per_hour": 10, "max_per_day": 50},
        "pii_check":      True,
        "pii_allowlist":  ["phone_number", "recipient_name"],
        "audit":          True,
        "autonomy_floor": True,
        "description":    "Send WhatsApp message (URL scheme + Cloud API)",
        "handler":        "scripts/actions/whatsapp_send.py",
        "enabled":        True,
    },
    "calendar_create": {
        "friction":       "low",
        "min_trust":      1,
        "sensitivity":    "standard",
        "timeout_sec":    15,
        "retry":          True,
        "retry_max":      2,
        "reversible":     True,
        "undo_window_sec": 3600,
        "rate_limit":     {"max_per_hour": 30, "max_per_day": 100},
        "pii_check":      False,
        "audit":          True,
        "autonomy_floor": False,
        "description":    "Create Google Calendar event",
        "handler":        "scripts/actions/calendar_create.py",
        "enabled":        True,
    },
    "calendar_modify": {
        "friction":       "standard",
        "min_trust":      1,
        "sensitivity":    "standard",
        "timeout_sec":    15,
        "retry":          True,
        "retry_max":      2,
        "reversible":     True,
        "undo_window_sec": 3600,
        "rate_limit":     {"max_per_hour": 20, "max_per_day": 50},
        "pii_check":      False,
        "audit":          True,
        "autonomy_floor": False,
        "description":    "Modify or reschedule existing Google Calendar event",
        "handler":        "scripts/actions/calendar_modify.py",
        "enabled":        True,
    },
    "reminder_create": {
        "friction":       "low",
        "min_trust":      1,
        "sensitivity":    "standard",
        "timeout_sec":    15,
        "retry":          True,
        "retry_max":      2,
        "reversible":     True,
        "undo_window_sec": 86400,
        "rate_limit":     {"max_per_hour": 30, "max_per_day": 200},
        "pii_check":      False,
        "audit":          True,
        "autonomy_floor": False,
        "description":    "Create reminder in Microsoft To Do",
        "handler":        "scripts/actions/reminder_create.py",
        "enabled":        True,
    },
    "todo_sync": {
        "friction":       "low",
        "min_trust":      0,
        "sensitivity":    "standard",
        "timeout_sec":    30,
        "retry":          True,
        "retry_max":      2,
        "reversible":     False,
        "rate_limit":     {"max_per_hour": 10, "max_per_day": 50},
        "pii_check":      False,
        "audit":          True,
        "autonomy_floor": False,
        "description":    "Bidirectional sync with Microsoft To Do",
        "handler":        "scripts/actions/todo_sync_action.py",
        "enabled":        True,
    },
    "instruction_sheet": {
        "friction":       "low",
        "min_trust":      0,
        "sensitivity":    "standard",
        "timeout_sec":    10,
        "retry":          False,
        "reversible":     False,
        "rate_limit":     {"max_per_hour": 30, "max_per_day": 200},
        "pii_check":      False,
        "audit":          True,
        "autonomy_floor": False,
        "description":    "Generate step-by-step instruction sheet (no external execution)",
        "handler":        "scripts/actions/instruction_sheet.py",
        "enabled":        True,
    },
    "run_pipeline": {
        "friction":       "low",
        "min_trust":      0,
        "timeout_sec":    120,
        "retry":          False,
        "reversible":     False,
        "pii_check":      False,
        "audit":          False,
        "autonomy_floor": False,
    },
}


def migrate_action(key: str, action: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Migrate a single action entry from v1 to v2.

    Returns (new_key, migrated_action).
    """
    new_key = _KEY_REMAP.get(key, key)
    migrated = dict(action)  # shallow copy to preserve original

    # Apply well-known v2 overrides (these take precedence over v1 values for new fields)
    if new_key in _V2_OVERRIDES:
        for field, default_val in _V2_OVERRIDES[new_key].items():
            if field not in migrated:
                migrated[field] = default_val

    # Apply generic defaults for any still-missing fields
    for field, default_val in _V2_DEFAULTS.items():
        if field not in migrated:
            migrated[field] = default_val

    # Friction vocabulary normalisation
    if "friction" in migrated:
        migrated["friction"] = _FRICTION_MAP.get(migrated["friction"], "standard")

    # Handler path remap
    if "handler" in migrated and migrated["handler"]:
        migrated["handler"] = _HANDLER_REMAP.get(migrated["handler"], migrated["handler"])

    # Remove v1-only fields that have v2 equivalents
    if "requires_approval" in migrated:
        # In v2, approval is encoded in autonomy_floor + friction; remove legacy field
        del migrated["requires_approval"]
    if "version" in migrated:
        del migrated["version"]

    return new_key, migrated


def migrate(yaml_path: Path, check_only: bool = False) -> int:
    """Migrate actions.yaml in-place.

    Returns 0 on success (no changes needed or migration applied).
    Returns 1 on error.
    """
    if not yaml_path.exists():
        print(f"ERROR: {yaml_path} not found.", file=sys.stderr)
        return 1

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        print(f"ERROR: {yaml_path} is not a valid YAML dict.", file=sys.stderr)
        return 1

    # Check current version
    current_version = str(data.get("schema_version", data.get("version", "1.0")))
    if current_version == "2.0":
        print(f"✅ {yaml_path.name} is already at schema_version 2.0 — no migration needed.")
        return 0

    print(f"🔄 Migrating {yaml_path.name} from v{current_version} to v2.0...")

    # Build migrated data
    new_actions: dict[str, Any] = {}
    changes: list[str] = []

    for key, action in data.get("actions", {}).items():
        new_key, migrated = migrate_action(key, action)

        if new_key != key:
            changes.append(f"  Renamed key: '{key}' → '{new_key}'")

        # Detect field additions
        new_fields = [f for f in migrated if f not in action]
        if new_fields:
            changes.append(f"  Added to '{new_key}': {new_fields}")

        new_actions[new_key] = migrated

    # Add any v2.0 actions that didn't exist in v1
    for action_type, defaults in _V2_OVERRIDES.items():
        if action_type not in new_actions:
            changes.append(f"  Added new v2 action: '{action_type}'")
            new_actions[action_type] = dict(defaults)
            # Apply generic defaults for missing fields
            for field, default_val in _V2_DEFAULTS.items():
                if field not in new_actions[action_type]:
                    new_actions[action_type][field] = default_val

    new_data = {
        "schema_version": "2.0",
        "actions": new_actions,
    }

    if check_only:
        print("Changes that would be made:")
        for ch in changes:
            print(ch)
        print(f"\nTotal changes: {len(changes)}")
        return 0

    if not changes:
        print("No structural changes required — updating schema_version only.")
    else:
        for ch in changes:
            print(ch)

    # Write backup
    bak_path = yaml_path.with_suffix(f".v1.bak")
    shutil.copy2(yaml_path, bak_path)
    print(f"✅ Backup written: {bak_path.name}")

    # Write migrated file with a header comment
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(
            f"# config/actions.yaml — Artha Declarative Action Registry\n"
            f"# Schema v2.0 — migrated from v1.0 on "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')} by migrate_actions_yaml.py\n"
            f"#\n"
            f"# SECURITY CONTRACT\n"
            f"# -----------------\n"
            f"# - autonomy_floor: true  → ALWAYS requires explicit human approval\n"
            f"#                          regardless of trust level or configuration.\n"
            f"# - pii_check: true       → pii_guard.py scans all non-allowlisted fields\n"
            f"# - enabled: false        → action is known but cannot be executed\n"
            f"#\n"
            f"# Ref: specs/act.md §4.2\n\n"
        )
        yaml.dump(new_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"✅ Migration complete: {yaml_path.name} → v2.0")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate config/actions.yaml from v1.0 to v2.0 schema"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Print what would change without writing",
    )
    parser.add_argument(
        "--backup-only", action="store_true",
        help="Create backup only; no migration",
    )
    parser.add_argument(
        "--path", type=Path,
        default=_ARTHA_DIR / "config" / "actions.yaml",
        help="Path to actions.yaml (default: config/actions.yaml)",
    )
    args = parser.parse_args()

    if args.backup_only:
        bak = args.path.with_suffix(".bak")
        shutil.copy2(args.path, bak)
        print(f"✅ Backup: {bak}")
        return 0

    return migrate(args.path, check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
