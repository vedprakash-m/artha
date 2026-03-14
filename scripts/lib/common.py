# pii-guard: ignore-file — shared infrastructure; no personal data
"""
scripts/lib/common.py — Canonical ARTHA_DIR and shared path constants.

All scripts that need ARTHA_DIR should import from here rather than
independently re-deriving it so that path logic is a single source of truth.

Usage:
    from scripts.lib.common import ARTHA_DIR, SCRIPTS_DIR, STATE_DIR, CONFIG_DIR

Ref: remediation.md §8, Issue M11, Issue M13, standardization.md §3.1
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

# Canonical project root — two levels up from scripts/lib/common.py
ARTHA_DIR: Path = Path(__file__).resolve().parent.parent.parent

SCRIPTS_DIR: Path = ARTHA_DIR / "scripts"
STATE_DIR:   Path = ARTHA_DIR / "state"
CONFIG_DIR:  Path = ARTHA_DIR / "config"
TMP_DIR:     Path = ARTHA_DIR / "tmp"


def update_channel_health_md(
    channel: str,
    healthy: bool,
    last_push: str | None = None,
    push_count_today: int | None = None,
) -> None:
    """Update the channel_health section in state/health-check.md.

    Called by:
    - preflight.check_channel_health() → updates last_check + healthy
    - channel_push._write_push_marker()  → updates last_push + push_count_today

    The section is a fenced YAML block under '## Channel Health (Structured)'.
    If the section doesn't exist it is appended. If the channel sub-entry
    doesn't exist it is added. Existing fields are updated in-place.
    """
    health_md = STATE_DIR / "health-check.md"
    if not health_md.exists():
        return

    now_iso = datetime.now(timezone.utc).isoformat()

    # Build the replacement channel block (indented 2 spaces under channel_health:)
    block_lines = [f"  {channel}:"]
    block_lines.append(f'    last_check: "{now_iso}"')
    block_lines.append(f"    healthy: {str(healthy).lower()}")
    if last_push is not None:
        block_lines.append(f'    last_push: "{last_push}"')
    if push_count_today is not None:
        block_lines.append(f"    push_count_today: {push_count_today}")
    new_ch_block = "\n".join(block_lines)

    content = health_md.read_text(encoding="utf-8")

    # Look for the entire channel_health fenced YAML block
    section_re = re.compile(
        r"(## Channel Health \(Structured\)\n```yaml\nchannel_health:\n)(.*?)(```)",
        re.DOTALL,
    )
    # Pattern to match this channel's existing sub-block inside the section
    ch_re = re.compile(
        rf"  {re.escape(channel)}:\n(?:    [^\n]*\n)*",
        re.MULTILINE,
    )

    m = section_re.search(content)
    if m:
        inner = m.group(2)
        if ch_re.search(inner):
            inner = ch_re.sub(new_ch_block + "\n", inner)
        else:
            inner = inner + new_ch_block + "\n"
        new_content = content[: m.start()] + m.group(1) + inner + m.group(3) + content[m.end() :]
    else:
        # Append new section at end of file
        append_block = (
            f"\n## Channel Health (Structured)\n"
            f"```yaml\nchannel_health:\n{new_ch_block}\n```\n"
        )
        new_content = content.rstrip() + "\n" + append_block

    try:
        health_md.write_text(new_content, encoding="utf-8")
    except OSError:
        pass  # Non-critical — don't crash callers
