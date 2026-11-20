#!/usr/bin/env python3
"""health_check_updater.py — Channel health section writer.

Extracted from lib/common.py (TD-10: domain logic in shared library).
Updates the channel_health fenced YAML block in state/health-check.md.

Called by:
- preflight.check_channel_health() → updates last_check + healthy
- channel_push._write_push_marker() → updates last_push + push_count_today

Ref: specs/pay-debt.md Phase 1 §5.1.2
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from lib.common import STATE_DIR


def update_channel_health_md(
    channel: str,
    healthy: bool,
    last_push: str | None = None,
    push_count_today: int | None = None,
) -> None:
    """Update the channel_health section in state/health-check.md.

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

    from lib.state_writer import StateWriteConflict, read_versioned, write

    try:
        content, expected_token = read_versioned(health_md)
    except (OSError, StateWriteConflict):
        return
    if not content.startswith("---"):
        content = (
            "---\nschema_version: '1.0'\ndomain: health-check\n"
            f"last_updated: '{now_iso[:10]}'\n---\n" + content
        )

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
        new_content = (
            content[: m.start()] + m.group(1) + inner + m.group(3) + content[m.end() :]
        )
    else:
        # Append new section at end of file
        append_block = (
            f"\n## Channel Health (Structured)\n"
            f"```yaml\nchannel_health:\n{new_ch_block}\n```\n"
        )
        new_content = content.rstrip() + "\n" + append_block

    try:
        result = write(
            health_md,
            new_content,
            domain="health-check",
            source="health_check_updater",
            expected_token=expected_token,
            pii_check=False,
            snapshot=health_md.resolve().is_relative_to(
                Path(__file__).resolve().parent.parent
            ),
        )
        if not result.success:
            return
    except (OSError, StateWriteConflict):
        return  # Callers see the machine-local refusal marker; no success-shaped fallback.
