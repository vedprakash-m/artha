#!/usr/bin/env python3
"""
migrate.py — Migrate settings.md to user_profile.yaml
======================================================
Best-effort migration helper for existing Artha installs that pre-date the
user_profile.yaml schema (Phase 0).

Reads config/settings.md YAML blocks and produces a pre-filled
config/user_profile.yaml modelled after config/user_profile.example.yaml.

SAFE — never overwrites an existing user_profile.yaml unless --force is passed.

Usage:
  python scripts/migrate.py                   # reads settings.md, writes user_profile.yaml
  python scripts/migrate.py --dry-run         # print generated YAML to stdout, no write
  python scripts/migrate.py --force           # overwrite existing user_profile.yaml
  python scripts/migrate.py --settings PATH   # use a custom settings.md path

Ref: standardization.md §6.3 Phase 2, T-2.2.y
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap — re-exec in venv if needed (same pattern as other scripts)
# ---------------------------------------------------------------------------
_ARTHA_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.name == "nt":
    _VENV_PY = Path.home() / ".artha-venvs" / ".venv-win" / "Scripts" / "python.exe"
else:
    _VENV_PY = Path.home() / ".artha-venvs" / ".venv" / "bin" / "python"
if (
    _VENV_PY.exists()
    and Path(sys.prefix).resolve() != _VENV_PY.parent.parent.resolve()
    and not os.environ.get("ARTHA_TEST_MODE")
):
    if os.name == "nt":
        import subprocess as _sp; raise SystemExit(_sp.call([str(_VENV_PY)] + sys.argv))
    else:
        os.execv(str(_VENV_PY), [str(_VENV_PY)] + sys.argv)

import yaml  # noqa: E402

# ---------------------------------------------------------------------------
# YAML block extraction from Markdown fenced code blocks
# ---------------------------------------------------------------------------

def _extract_yaml_blocks(md_text: str) -> list[dict[str, Any]]:
    """Extract all ```yaml ... ``` blocks from a Markdown file."""
    pattern = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
    results = []
    for match in pattern.finditer(md_text):
        try:
            parsed = yaml.safe_load(match.group(1))
            if isinstance(parsed, dict):
                results.append(parsed)
        except yaml.YAMLError:
            pass
    return results


def _merge_blocks(blocks: list[dict]) -> dict:
    """Shallow-merge all YAML blocks into one flat dict."""
    merged: dict = {}
    for block in blocks:
        merged.update(block)
    return merged


# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------

def _get(d: dict, *keys, default=None):
    """Deep-get from nested dict using dot-path or varargs."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    return cur if cur is not None else default


def _extract_family(data: dict) -> dict:
    """Build the family block from merged settings data."""
    primary_user_str: str = data.get("primary_user", "")
    # e.g. "John (Johnny)" → name=John, nickname=Johnny
    nick_match = re.match(r"^(\w+)\s*\((\w+)\)", primary_user_str.strip())
    if nick_match:
        name = nick_match.group(1)
        nickname = nick_match.group(2)
    else:
        name = primary_user_str.strip().split()[0] if primary_user_str.strip() else ""
        nickname = name

    gmail = data.get("briefing_email", "")
    outlook = data.get("outlook_email", "")

    # Extract family members from family_members list (role-keyed) or top-level children key
    members: list[dict] = data.get("family_members", [])
    spouse_name = ""
    children_from_members = []
    for m in members:
        role = m.get("role", "")
        if role == "spouse":
            spouse_name = m.get("name", "")
        elif role == "child":
            children_from_members.append(m)

    # Top-level "children" key takes precedence over family_members role=child entries
    children_raw: list[dict] = data.get("children") or children_from_members

    return {
        "name": data.get("family_name", ""),
        "cultural_context": "",
        "primary_user": {
            "name": name,
            "nickname": nickname,
            "role": "primary",
            "emails": {
                "gmail": gmail,
                "outlook": outlook,
                "icloud": "",
            },
            "phone": "",
        },
        "spouse": {
            "enabled": bool(spouse_name),
            "name": spouse_name,
            "role": "spouse",
            "filtered_briefing": True,
        },
        "children": [_build_child(c) for c in children_raw],
    }


def _build_child(m: dict) -> dict:
    return {
        "name": m.get("name", ""),
        "age": m.get("age", None),
        "grade": "",
        "school": {
            "name": "",
            "district": "",
            "canvas_url": "",
            "canvas_keychain_key": "",
        },
        "milestones": {
            "college_prep": False,
            "class_of": None,
            "new_driver": False,
        },
    }


def _extract_location(data: dict) -> dict:
    tz = data.get("timezone", "America/Los_Angeles")
    return {
        "city": data.get("city", ""),
        "state": data.get("state", ""),
        "county": data.get("county", ""),
        "country": data.get("country", "US"),
        "lat": data.get("lat", 0.0),
        "lon": data.get("lon", 0.0),
        "timezone": tz,
        "property_tax_provider": "king_county",
        "parcel_id": "",
    }


def _extract_domains(data: dict) -> dict:
    caps = data.get("capabilities", {})

    def _enabled(cap_key: str, default: bool = True) -> bool:
        v = caps.get(cap_key)
        return bool(v) if v is not None else default

    return {
        "immigration": {"enabled": True, "context": ""},
        "finance": {
            "enabled": True,
            "institutions": [],
            "alert_thresholds": {"bill_due_days": 7, "low_balance_usd": 1000},
        },
        "kids": {"enabled": True},
        "health": {"enabled": True},
        "travel": {"enabled": True},
        "home": {"enabled": True},
        "shopping": {"enabled": True},
        "goals": {"enabled": True},
        "vehicle": {
            "enabled": True,
            "vehicles": [],
        },
        "estate": {"enabled": False},
        "insurance": {"enabled": True},
        "calendar": {"enabled": _enabled("calendar_mcp")},
        "comms": {"enabled": True},
        "social": {"enabled": True},
        "digital": {"enabled": True},
        "boundary": {"enabled": False},
        "learning": {"enabled": False},
        "employment": {
            "enabled": False,
            "employer": "",
            "workiq_enabled": _enabled("workiq_calendar", default=False),
        },
    }


def _extract_integrations(data: dict) -> dict:
    gmail = data.get("briefing_email", "")
    family_cal = _get(data, "calendars", "family", default="")
    holidays_cal = _get(data, "calendars", "holidays",
                        default="en.usa#holiday@group.v.calendar.google.com")
    return {
        "gmail": {"enabled": True, "account": gmail},
        "google_calendar": {
            "enabled": True,
            "calendar_ids": {
                "primary": "primary",
                "additional": [family_cal] if family_cal else [],
                "holidays": holidays_cal,
            },
        },
        "microsoft_graph": {
            "enabled": bool(data.get("capabilities", {}).get("todo_sync")),
            "account": "",
            "todo_sync": bool(data.get("capabilities", {}).get("todo_sync")),
        },
        "icloud": {
            "enabled": bool(data.get("capabilities", {}).get("icloud_direct_api")),
            "account": "",
        },
        "canvas_lms": {"enabled": True},
        "workiq": {
            "enabled": bool(data.get("capabilities", {}).get("workiq_calendar")),
            "platform": "windows",
        },
    }


def _extract_system(data: dict) -> dict:
    tz = data.get("timezone", "America/Los_Angeles")
    return {
        "artha_dir": "",
        "sync_provider": "onedrive",
        "venv_path": "~/.artha-venvs/.venv",
        "venv_path_win": "~/.artha-venvs/.venv-win",
        "python_cmd": "python3",
        "briefing_timezone": tz,
        "cost_budget_monthly_usd": data.get("monthly_api_budget_usd", 25),
    }


def _extract_briefing(data: dict) -> dict:
    gmail = data.get("briefing_email", "")
    tz = data.get("timezone", "America/Los_Angeles")
    return {
        "email": gmail,
        "timezone": tz,
        "default_format": "standard",
        "spouse_filtered": True,
        "email_enabled": bool(data.get("capabilities", {}).get("email_briefings")),
        "archive_enabled": True,
        "weekend_planner": True,
        "monthly_retrospective": True,
    }


def _extract_budget(data: dict) -> dict:
    return {
        "monthly_api_budget_usd": data.get("monthly_api_budget_usd", 25),
        "alert_at_percent": data.get("alert_at_percent", 80),
        "currency": data.get("currency", "USD"),
    }


def _extract_encryption(data: dict) -> dict:
    return {
        "age_recipient": data.get("age_recipient", ""),
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_profile(settings_data: dict) -> dict:
    return {
        "schema_version": "1.0",
        "family": _extract_family(settings_data),
        "location": _extract_location(settings_data),
        "domains": _extract_domains(settings_data),
        "system": _extract_system(settings_data),
        "integrations": _extract_integrations(settings_data),
        "briefing": _extract_briefing(settings_data),
        "budget": _extract_budget(settings_data),
        "encryption": _extract_encryption(settings_data),
    }


def _yaml_header() -> str:
    return """\
# config/user_profile.yaml
# ─────────────────────────────────────────────────────────────────────────────
# Generated by scripts/migrate.py from config/settings.md.
# Review every field — leave blanks where you are unsure.
# This file is NEVER committed to git (see .gitignore).
# ─────────────────────────────────────────────────────────────────────────────

"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def migrate(
    settings_path: "Path | None" = None,
    output_path: "Path | None" = None,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    """Programmatic migration entry point — usable from tests and other scripts."""
    if settings_path is None:
        settings_path = _ARTHA_DIR / "config" / "settings.md"
    if output_path is None:
        output_path = _ARTHA_DIR / "config" / "user_profile.yaml"

    if not settings_path.exists():
        print(f"ERROR: settings file not found: {settings_path}", file=sys.stderr)
        sys.exit(1)

    if output_path.exists() and not dry_run and not force:
        print(
            f"ERROR: {output_path} already exists.\n"
            "       Pass --force to overwrite, or --dry-run to preview.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Parse
    md_text = settings_path.read_text(encoding="utf-8")
    blocks = _extract_yaml_blocks(md_text)
    if not blocks:
        print("WARNING: No YAML blocks found in settings.md — profile will be mostly empty.")
    merged = _merge_blocks(blocks)

    profile = build_profile(merged)

    # Render
    output_yaml = _yaml_header() + yaml.dump(
        profile,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )

    if dry_run:
        print(output_yaml)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output_yaml, encoding="utf-8")
    print(f"✓ Wrote {output_path}")
    print()
    print("Next steps:")
    print("  1. Open config/user_profile.yaml and fill in any empty fields (location, children details, etc.)")
    print("  2. Run: python scripts/generate_identity.py --validate")
    print("  3. Run: python scripts/generate_identity.py")
    print("  4. Run: python scripts/preflight.py")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate config/settings.md to config/user_profile.yaml"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated YAML to stdout without writing any files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing user_profile.yaml",
    )
    parser.add_argument(
        "--settings",
        metavar="PATH",
        default=str(_ARTHA_DIR / "config" / "settings.md"),
        help="Path to settings.md (default: config/settings.md)",
    )
    args = parser.parse_args()

    migrate(
        settings_path=Path(args.settings),
        output_path=_ARTHA_DIR / "config" / "user_profile.yaml",
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    main()
