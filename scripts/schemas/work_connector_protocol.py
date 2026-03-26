"""
scripts/schemas/work_connector_protocol.py — Connector error protocol for Work OS.

Implements §8.4: defines failure modes, fallback behaviour, and user-facing
signals for all work connectors. This is the equivalent of AR-8 for the
Work OS's connector layer.

Rules:
  1. No single connector failure blocks the entire /work workflow.
  2. Cached state is always preferable to no output.
  3. Error messages are actionable — tell the user what to do, not what failed.
  4. Every failure is logged to state/work/work-audit.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Failure mode taxonomy
# ---------------------------------------------------------------------------

class ConnectorFailureMode(str, Enum):
    """Enumerated failure modes for §8.4 fallback routing."""
    TIMEOUT          = "timeout"           # network/process timeout
    AUTH_EXPIRED     = "auth_expired"      # token / credential expired
    AUTH_INVALID     = "auth_invalid"      # re-auth required
    PERMISSION_ERROR = "permission_error"  # scope denied (e.g. Graph 403)
    RATE_LIMITED     = "rate_limited"      # provider throttle
    PLATFORM_SKIP    = "platform_skip"     # connector run_on constraint not met
    PARSE_ERROR      = "parse_error"       # unexpected provider response
    UNAVAILABLE      = "unavailable"       # connector binary / service down
    ALL_DOWN         = "all_down"          # every provider failed


# ---------------------------------------------------------------------------
# Protocol entries — one per connector type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConnectorProtocolEntry:
    """Maps a (connector, failure_mode) pair to fallback and user signal."""
    connector: str                         # matches connectors.yaml name
    failure_mode: ConnectorFailureMode
    fallback_action: str                   # what the system does automatically
    user_signal: str                       # human-readable status line for /work health
    remediation: str                       # what the user should do
    blocks_workflow: bool = False          # if true, halt entire /work run


# §8.4 protocol table — canonical source
PROTOCOL: tuple[ConnectorProtocolEntry, ...] = (
    # WorkIQ
    ConnectorProtocolEntry(
        connector="workiq_bridge",
        failure_mode=ConnectorFailureMode.TIMEOUT,
        fallback_action="skip WorkIQ queries; use Graph providers if available; use cached state",
        user_signal="⚠ WorkIQ unavailable — using cached data",
        remediation="WorkIQ will retry on next /work refresh. If persistent, run: npx -y @microsoft/workiq@latest doctor",
    ),
    ConnectorProtocolEntry(
        connector="workiq_bridge",
        failure_mode=ConnectorFailureMode.PLATFORM_SKIP,
        fallback_action="skip WorkIQ silently (non-Windows platform)",
        user_signal="ℹ WorkIQ skipped (Windows only) — using Graph fallback",
        remediation="Run /work on Windows to enable WorkIQ enrichment.",
    ),

    # ADO
    ConnectorProtocolEntry(
        connector="ado_workitems",
        failure_mode=ConnectorFailureMode.AUTH_EXPIRED,
        fallback_action="attempt az account get-access-token refresh; if that fails, use cached state",
        user_signal="⚠ ADO auth expired — using cached project data",
        remediation="Run: az login  (then /work refresh to update project data)",
    ),
    ConnectorProtocolEntry(
        connector="ado_workitems",
        failure_mode=ConnectorFailureMode.AUTH_INVALID,
        fallback_action="skip ADO queries; use cached state",
        user_signal="🔴 ADO authentication invalid",
        remediation="Run: az login --tenant <tenant-id>  then /work refresh",
        blocks_workflow=False,
    ),

    # MS Graph — mail scope
    ConnectorProtocolEntry(
        connector="msgraph_email",
        failure_mode=ConnectorFailureMode.PERMISSION_ERROR,
        fallback_action="skip mail data category; proceed with remaining Graph scopes",
        user_signal="⚠ Graph: Mail.Read unavailable — email data partial",
        remediation="Re-consent Graph OAuth including Mail.Read scope. Run: scripts/setup_msgraph_oauth.py",
    ),

    # MS Graph — calendar scope
    ConnectorProtocolEntry(
        connector="msgraph_calendar",
        failure_mode=ConnectorFailureMode.PERMISSION_ERROR,
        fallback_action="fall back to WorkIQ calendar if available; otherwise use cached state",
        user_signal="⚠ Graph: Calendars.Read unavailable — using WorkIQ calendar fallback",
        remediation="Re-consent Graph OAuth including Calendars.Read scope.",
    ),
    ConnectorProtocolEntry(
        connector="msgraph_calendar",
        failure_mode=ConnectorFailureMode.AUTH_INVALID,
        fallback_action="halt Graph queries; use cached calendar state",
        user_signal="🔴 Graph re-auth required",
        remediation="Run: scripts/setup_msgraph_oauth.py to re-authenticate",
    ),

    # Outlook COM bridge (Windows only)
    ConnectorProtocolEntry(
        connector="outlookctl_bridge",
        failure_mode=ConnectorFailureMode.PLATFORM_SKIP,
        fallback_action="skip silently (Windows only)",
        user_signal="ℹ Outlook bridge skipped (Windows only)",
        remediation="Run /work on Windows to enable Outlook COM enrichment.",
    ),
    ConnectorProtocolEntry(
        connector="outlookctl_bridge",
        failure_mode=ConnectorFailureMode.UNAVAILABLE,
        fallback_action="use Graph calendar fallback",
        user_signal="⚠ Outlook bridge unavailable — using Graph calendar",
        remediation="Ensure Classic Outlook (Wave 2) is installed and running.",
    ),

    # All providers down (§8.4 "all providers down" case)
    ConnectorProtocolEntry(
        connector="*",
        failure_mode=ConnectorFailureMode.ALL_DOWN,
        fallback_action="serve from cached summary state only",
        user_signal="⚠ Work OS offline — showing last known state from [timestamp]",
        remediation="Check network connectivity then run /work refresh. See /work health for details.",
        blocks_workflow=False,
    ),
)

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def get_protocol(connector: str, failure_mode: ConnectorFailureMode) -> Optional[ConnectorProtocolEntry]:
    """Return the protocol entry for (connector, failure_mode), or the wildcard entry."""
    for entry in PROTOCOL:
        if entry.connector == connector and entry.failure_mode == failure_mode:
            return entry
    # fall back to wildcard
    for entry in PROTOCOL:
        if entry.connector == "*" and entry.failure_mode == failure_mode:
            return entry
    return None


def user_signal_for(connector: str, failure_mode: ConnectorFailureMode) -> str:
    """Return the human-readable status line for a connector failure."""
    entry = get_protocol(connector, failure_mode)
    if entry:
        return entry.user_signal
    return f"⚠ {connector}: degraded ({failure_mode.value})"


# ---------------------------------------------------------------------------
# Audit log helper (writes to state/work/work-audit.md)
# ---------------------------------------------------------------------------

def log_connector_failure(
    connector: str,
    failure_mode: ConnectorFailureMode,
    detail: str = "",
    audit_path: Optional[object] = None,  # pathlib.Path
) -> None:
    """
    Append a connector failure entry to state/work/work-audit.md.
    Non-blocking — if the write fails, the failure is silently swallowed
    to avoid cascading errors during degraded-mode operation.
    """
    from datetime import datetime, timezone
    from pathlib import Path

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = get_protocol(connector, failure_mode)
    signal = entry.user_signal if entry else f"degraded ({failure_mode.value})"
    line = f"| {ts} | {connector} | {failure_mode.value} | {signal} | {detail[:120]} |\n"

    if audit_path is None:
        audit_path = Path(__file__).resolve().parents[2] / "state" / "work" / "work-audit.md"

    try:
        audit_path = Path(audit_path)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass  # never block the workflow due to audit write failure
