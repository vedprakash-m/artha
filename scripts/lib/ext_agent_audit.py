"""AR-9 External Agent Composition — Audit Event Writer (§9.5, Appendix B).

Appends structured audit rows to state/work/work-audit.md.
All operations are fire-and-forget — this module NEVER raises.

Supported event types
---------------------
EXT_AGENT_ROUTED      — router matched agent for a query
EXT_AGENT_INVOKED     — agent was invoked (success or failure)
EXT_AGENT_INJECTION   — injection detected in agent response
EXT_AGENT_UPDATE      — agent definition updated (hash change)
EXT_AGENT_HEALTH      — health state transition (active→degraded, etc.)

Row format (appended to existing Markdown table)
-------------------------------------------------
| <ISO-8601 UTC> | <agent_name> | <event_type> | AR-9 | <detail[:120]> |
"""

# pii-guard: ignore-file
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_AUDIT_FILE: Path = (
    Path(__file__).resolve().parent.parent.parent
    / "state"
    / "work"
    / "work-audit.md"
)

_DETAIL_MAX_CHARS = 120


def write_ext_agent_event(
    event_type: str,
    agent_name: str,
    detail: str,
    audit_file: Path | None = None,
) -> None:
    """Append one AR-9 audit event row to work-audit.md.

    Fire-and-forget — never raises; all errors are silently swallowed.

    Parameters
    ----------
    event_type:
        One of EXT_AGENT_ROUTED / EXT_AGENT_INVOKED / EXT_AGENT_INJECTION /
        EXT_AGENT_UPDATE / EXT_AGENT_HEALTH.
    agent_name:
        Registered agent slug (e.g. ``storage-deployment-expert``).
    detail:
        Free-form detail string; truncated to 120 chars.
    audit_file:
        Override target file — used in tests for isolation.
        Defaults to ``state/work/work-audit.md``.
    """
    target: Path = audit_file if audit_file is not None else _DEFAULT_AUDIT_FILE
    try:
        if not target.exists():
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = (
            f"| {ts} | {agent_name} | {event_type} | AR-9 |"
            f" {detail[:_DETAIL_MAX_CHARS]} |\n"
        )
        with target.open("a", encoding="utf-8") as fh:
            fh.write(row)
    except OSError:
        pass
