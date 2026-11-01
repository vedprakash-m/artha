#!/usr/bin/env python3
# pii-guard: names and contact data are PII — pii_guard applied on all outbound output
"""
scripts/relationship_pulse_view.py — /relationships command view renderer (E13).

Reads state/relationships.md and renders a formatted relationship health summary
for the /relationships channel command. Backed by state/relationships.md.

Usage:
  python scripts/relationship_pulse_view.py
  python scripts/relationship_pulse_view.py --format flash
  python scripts/relationship_pulse_view.py --format standard  (default)

Output: Markdown-formatted relationship health card ready for display.

Exit codes:
  0 — success
  1 — state file missing or empty (bootstrapping needed)

Follows dashboard_view.py structural pattern.

Config flag: enhancements.relationship_pulse (default: true)

Ref: specs/act-reloaded.md Enhancement 13, config/commands.md /relationships
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
_STATE_DIR = _ARTHA_DIR / "state"

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False

try:
    from context_offloader import load_harness_flag as _load_flag  # type: ignore[import]
except ImportError:  # pragma: no cover
    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

# PII patterns for outbound filtering
_PII_STRIP = [
    (re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"), "[SSN]"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
]


def _pii_filter(text: str) -> str:
    for pat, repl in _PII_STRIP:
        text = pat.sub(repl, text)
    return text


def _staleness_note(last_updated: str) -> str:
    """Return staleness string for a state file's last_updated timestamp."""
    if not last_updated:
        return "_Last updated: never_"
    try:
        lu = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        delta = datetime.now(tz=timezone.utc) - lu
        hours = int(delta.total_seconds() // 3600)
        if hours < 2:
            return "_Last updated: just now_"
        if hours < 48:
            return f"_Last updated: {hours}h ago_"
        days = hours // 24
        return f"_Last updated: {days}d ago_"
    except Exception:
        return f"_Last updated: {last_updated}_"


def _parse_table_section(content: str, section_header: str) -> list[list[str]]:
    """Extract rows from a Markdown table under a given section header."""
    rows = []
    in_section = False
    in_table = False

    for line in content.splitlines():
        if line.strip().startswith("## ") or line.strip().startswith("# "):
            if section_header.lower() in line.lower():
                in_section = True
                in_table = False
                continue
            elif in_section:
                break  # moved past our section
            continue

        if not in_section:
            continue

        if line.strip().startswith("|") and "---" not in line:
            parts = [p.strip() for p in line.strip("|").split("|")]
            if parts and not parts[0].lower() in ("name", "person"):
                rows.append(parts)
            in_table = True
        elif in_table and not line.strip().startswith("|"):
            break

    return [r for r in rows if any(c.strip() for c in r)]


def render_relationships(fmt: str = "standard") -> tuple[str, int]:
    """Render relationship health view. Returns (text, exit_code)."""
    if not _load_flag("enhancements.relationship_pulse", default=True):
        return "ℹ️  Relationship pulse is disabled (enhancements.relationship_pulse: false)", 0

    rel_path = _STATE_DIR / "relationships.md"
    if not rel_path.exists():
        return (
            "⚠️ *state/relationships.md not found.*\n\n"
            "Run `/bootstrap relationships` to set up your relationship tracker.",
            1,
        )

    content = rel_path.read_text(encoding="utf-8")
    last_updated = ""

    # Extract frontmatter last_updated
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm_match and _YAML_AVAILABLE:
        try:
            fm = yaml.safe_load(fm_match.group(1)) or {}
            last_updated = str(fm.get("last_updated", ""))
        except Exception:
            pass

    inner_circle = _parse_table_section(content, "Inner Circle")
    extended = _parse_table_section(content, "Extended")
    occasions = _parse_table_section(content, "Upcoming Occasions")

    today = date.today()
    lines = ["## 👥 Relationship Pulse", ""]

    # Count overdue contacts (last contact > cadence threshold)
    overdue: list[str] = []
    for row in inner_circle:
        if len(row) < 4:
            continue
        name, relation, last_contact, *_ = row
        if not name or not last_contact:
            continue
        try:
            lc_date = date.fromisoformat(last_contact.strip())
            days_since = (today - lc_date).days
            if days_since > 30:
                overdue.append(f"{name} ({days_since}d)")
        except ValueError:
            pass

    for row in extended:
        if len(row) < 4:
            continue
        name, *_, last_contact, next_out, notes = (row + ["", "", ""])[:6]
        if not name or not last_contact:
            continue
        try:
            lc_date = date.fromisoformat(last_contact.strip())
            days_since = (today - lc_date).days
            if days_since > 90:
                overdue.append(f"{name} ({days_since}d)")
        except ValueError:
            pass

    # Upcoming occasions (within 14 days)
    upcoming: list[str] = []
    for row in occasions:
        if len(row) < 3:
            continue
        person, event, occ_date, *_ = (row + ["", ""])[:5]
        if not occ_date:
            continue
        try:
            ev_date = date.fromisoformat(occ_date.strip())
            days_until = (ev_date - today).days
            if 0 <= days_until <= 14:
                upcoming.append(f"{person} — {event} ({days_until}d)")
        except ValueError:
            pass

    # Format output
    if fmt == "flash":
        summary_parts = []
        if overdue:
            summary_parts.append(f"🟠 {len(overdue)} overdue contacts")
        if upcoming:
            summary_parts.append(f"🎂 {len(upcoming)} upcoming occasions")
        if not summary_parts:
            summary_parts.append("✅ All relationships current")
        lines.append(" · ".join(summary_parts))
    else:
        # Standard format
        if not inner_circle and not extended and not occasions:
            lines.append(
                "📋 *No relationships configured yet.*\n\n"
                "Use `/bootstrap relationships` to set up your relationship tracker,\n"
                "or edit `state/relationships.md` directly."
            )
        else:
            if overdue:
                lines.append(f"**🟠 Overdue Contacts ({len(overdue)})**")
                for name in overdue[:5]:
                    lines.append(f"  - {_pii_filter(name)}")
                lines.append("")

            if upcoming:
                lines.append(f"**🎂 Upcoming Occasions ({len(upcoming)})**")
                for item in upcoming[:5]:
                    lines.append(f"  - {_pii_filter(item)}")
                lines.append("")

            if inner_circle:
                lines.append(f"**👨‍👩‍👧‍👦 Inner Circle** — {len(inner_circle)} contacts")
            if extended:
                lines.append(f"**🌐 Extended** — {len(extended)} contacts")

            if not overdue and not upcoming:
                lines.append("✅ All relationships on cadence")

    lines.append("")
    lines.append(_staleness_note(last_updated))

    return _pii_filter("\n".join(lines)), 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Relationship pulse view")
    parser.add_argument("--format", choices=["standard", "flash"], default="standard")
    args = parser.parse_args(argv)

    text, code = render_relationships(fmt=args.format)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
