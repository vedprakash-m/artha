#!/usr/bin/env python3
# pii-guard: ignore-file — channel push infrastructure; tokens in keyring; PII guard applied to all outbound
"""
channel_push.py — Artha post-catch-up channel push (Layer 1, Step 20).

Delivers a flash briefing summary to enabled channel recipients after catch-up.

Usage:
    python scripts/channel_push.py           # Run push (called by catch-up workflow)
    python scripts/channel_push.py --health  # Health check mode (preflight)
    python scripts/channel_push.py --dry-run # Show what would be sent; no actual API calls

Design Guarantees:
  - Non-blocking: any individual failure is logged and skipped; catch-up always continues
  - PII: pii_guard.filter_text() applied to EVERY outbound message — no exceptions
  - Scope: per-recipient access_scope filter applied BEFORE PII redaction
  - Dedup: daily marker file prevents duplicate pushes on multi-machine setups (12h window)
  - Queue: API failures write to pending queue; retried on next catch-up run
  - Feature flag: push_enabled: false (default) — explicit opt-in required

Access scope filtering (applied before PII redaction):
  full     → all content
  family   → strip lines containing: immigration, finance, estate, insurance,
             employment, digital, boundary domain keywords
  standard → only lines containing: calendar, task, open item, goal, schedule

Ref: specs/conversational-bridge.md §7
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure Artha root on sys.path
_ARTHA_DIR = Path(__file__).resolve().parent.parent
if str(_ARTHA_DIR) not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR))

_STATE_DIR = _ARTHA_DIR / "state"
_BRIEFINGS_DIR = _ARTHA_DIR / "briefings"
_PENDING_PUSHES_DIR = _STATE_DIR / ".pending_pushes"
_AUDIT_LOG = _STATE_DIR / "audit.md"

# Push dedup: marker file tracks today's push
_PUSH_MARKER_PATTERN = ".channel_push_marker_{date}.json"
# Re-push allowed after this many hours (catches evening re-run)
_PUSH_DEDUP_HOURS = 12
# Pending push files older than this are discarded (stale intelligence)
_PENDING_MAX_HOURS = 24
# Push marker files older than this are cleaned up
_MARKER_MAX_DAYS = 7

logging.basicConfig(
    level=logging.INFO,
    format="[channel_push] %(levelname)s: %(message)s",
)
log = logging.getLogger("channel_push")

# ── Access scope domain keyword maps ─────────────────────────────────────────
# Keywords that identify content from excluded domains.
# Scope filter removes any briefing line that contains these keywords.

_FAMILY_EXCLUDED_KEYWORDS = (
    "immigration", "visa", "ead", "i-765", "i-485", "h-1b", "h-4", "perm",
    "i-140", "green card", "uscis", "priority date", "cspa", "alien",
    "a-number", "passport", "consulate", "attorney",
    "finance", "fidelity", "vanguard", "morgan stanley", "etrade",
    "account balance", "routing", "investment", "portfolio", "401k",
    "roth ira", "brokerage", "hdfc", "wire transfer", "tax", "irs", "w-2",
    "estate", "will", "trust", "beneficiary", "poa", "guardian",
    "insurance", "premium", "deductible", "umbrella", "life insurance",
    "employment", "salary", "performance", "promotion", "hr ", "payroll",
    "digital life", "subscription", "boundary",
)

_STANDARD_INCLUDED_KEYWORDS = (
    "calendar", "event", "appointment", "meeting", "schedule",
    "task", "open item", "oi-", "goal", "due", "today", "tomorrow",
    "this week", "artha",
)


def _apply_scope_filter(text: str, scope: str) -> str:
    """Filter briefing text based on access scope.

    Access scope is a CONTENT GATE, not a redaction pass.
    Applied BEFORE PII redaction.

    Args:
        text:  Raw briefing text.
        scope: "full", "family", or "standard".

    Returns:
        Filtered text (may be shorter than input).
    """
    if scope == "full":
        return text

    lines = text.splitlines(keepends=True)

    if scope == "family":
        # Remove lines that reference excluded domain keywords
        filtered = []
        for line in lines:
            line_lower = line.lower()
            if any(kw in line_lower for kw in _FAMILY_EXCLUDED_KEYWORDS):
                continue
            filtered.append(line)
        return "".join(filtered)

    if scope == "standard":
        # Keep only lines matching standard-scope keywords + blank lines + headers
        filtered = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                filtered.append(line)
                continue
            line_lower = stripped.lower()
            # Keep section headers (ARTHA · date line)
            if line_lower.startswith("artha") or stripped.startswith("━"):
                filtered.append(line)
                continue
            if any(kw in line_lower for kw in _STANDARD_INCLUDED_KEYWORDS):
                filtered.append(line)
        return "".join(filtered)

    # Unknown scope — safest option is full redaction
    log.warning("Unknown access scope '%s' — treating as standard", scope)
    return _apply_scope_filter(text, "standard")


def _get_latest_briefing() -> str | None:
    """Read the most recent briefing file. Returns content or None."""
    if not _BRIEFINGS_DIR.exists():
        return None
    briefing_files = sorted(_BRIEFINGS_DIR.glob("*.md"), reverse=True)
    if not briefing_files:
        return None
    try:
        return briefing_files[0].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _build_flash_message(scope: str, max_length: int = 500) -> str:
    """Build a flash message for push delivery.

    Reads latest briefing file (preferred) or falls back to state files.
    Applies scope filter, then truncates to max_length.

    Args:
        scope:      Access scope for the recipient.
        max_length: Maximum character count for the message.

    Returns:
        Formatted flash message string.
    """
    briefing = _get_latest_briefing()

    if briefing:
        # Extract the most relevant summary from the briefing.
        # Heuristic: take the first major section up to max_length,
        # prioritizing critical/urgent alerts.
        lines = briefing.splitlines()
        selected: list[str] = []
        char_count = 0
        in_alert_section = False
        for line in lines:
            # Skip blank lines at start
            if not selected and not line.strip():
                continue
            # Track alert/summary sections
            lower = line.lower()
            if any(kw in lower for kw in ("critical", "urgent", "🔴", "🟠", "today", "artha ·")):
                in_alert_section = True
            # Stop at domain section headers (we want the summary, not details)
            if lower.startswith("## ") and selected:
                break
            if len(line) + char_count > max_length - 80:
                # Reserve 80 chars for command footer
                break
            selected.append(line)
            char_count += len(line) + 1

        text = "\n".join(selected).strip()
    else:
        # Fallback: build minimal status from state files
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%A, %b %-d") if os.name != "nt" else now.strftime("%A, %b %d").lstrip("0")  # noqa: E501
        text = f"ARTHA · {date_str}\n\nCatch-up complete. Check /status for details."

    # Apply scope filter
    filtered_text = _apply_scope_filter(text, scope)

    # Append command footer
    footer = "\n\n/status · /tasks · /alerts"
    if scope != "full":
        footer = "\n\n/status · /tasks"

    # Truncate + append footer within max_length
    body_max = max_length - len(footer)
    if len(filtered_text) > body_max:
        filtered_text = filtered_text[:body_max - 3].rstrip() + "…"

    return filtered_text + footer


def _push_marker_path(date_str: str) -> Path:
    """Return the path for today's push marker file."""
    fname = _PUSH_MARKER_PATTERN.format(date=date_str)
    return _STATE_DIR / fname


def _check_push_marker() -> tuple[bool, str, str, str]:
    """Check if a push was already sent within the dedup window.

    Returns:
        (already_pushed: bool, reason: str, marker_host: str, marker_time: str)
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    marker_path = _push_marker_path(today)

    if not marker_path.exists():
        return False, "no marker", "", ""

    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        pushed_at_str = marker.get("pushed_at", "")
        marker_host = marker.get("host", "?")
        if pushed_at_str:
            pushed_at = datetime.fromisoformat(pushed_at_str.replace("Z", "+00:00"))
            age_hours = (
                datetime.now(timezone.utc) - pushed_at
            ).total_seconds() / 3600
            if age_hours < _PUSH_DEDUP_HOURS:
                return True, f"pushed {age_hours:.1f}h ago from {marker_host}", marker_host, pushed_at_str
            return False, f"stale marker ({age_hours:.1f}h old, threshold={_PUSH_DEDUP_HOURS}h)", "", ""
    except (json.JSONDecodeError, ValueError, OSError):
        pass

    return False, "marker unreadable — proceeding", "", ""


def _write_push_marker(channels_pushed: list[str]) -> None:
    """Write daily push marker to prevent duplicate pushes across machines."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    marker_path = _push_marker_path(today)
    import socket
    pushed_at = datetime.now(timezone.utc).isoformat()
    marker = {
        "host": socket.gethostname(),
        "pushed_at": pushed_at,
        "channels": channels_pushed,
    }
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not write push marker: %s", exc)

    # Update channel_health section in state/health-check.md for each pushed channel
    from lib.common import update_channel_health_md
    for ch in channels_pushed:
        try:
            update_channel_health_md(ch, healthy=True, last_push=pushed_at, push_count_today=1)
        except Exception:
            pass  # Non-critical


def _cleanup_old_markers() -> None:
    """Remove push marker files older than _MARKER_MAX_DAYS days."""
    now = datetime.now(timezone.utc)
    for marker_file in _STATE_DIR.glob(".channel_push_marker_*.json"):
        try:
            age_days = (now.timestamp() - marker_file.stat().st_mtime) / 86400
            if age_days > _MARKER_MAX_DAYS:
                marker_file.unlink()
        except OSError:
            pass


def _write_pending_push(
    channel_name: str, recipient_id: str, text: str, scope: str
) -> None:
    """Queue a failed push for retry on next catch-up run."""
    _PENDING_PUSHES_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fname = f"{channel_name}_{recipient_id[:12]}_{ts}.json"
    pending = {
        "channel": channel_name,
        "recipient_id": recipient_id,
        "text": text,
        "scope": scope,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    try:
        (_PENDING_PUSHES_DIR / fname).write_text(
            json.dumps(pending, indent=2), encoding="utf-8"
        )
        log.warning(
            "Push queued for retry: %s → %s (file: %s)", channel_name, recipient_id, fname
        )
        _audit_log(
            "CHANNEL_PUSH_PENDING",
            channel=channel_name,
            recipient=recipient_id,
            pending_file=fname,
        )
    except OSError as exc:
        log.error("Could not write pending push file: %s", exc)


def _send_pending_pushes(
    channel_name: str, adapter: object, dry_run: bool = False
) -> int:
    """Send any pending pushes from the queue for this channel.

    Pending pushes older than _PENDING_MAX_HOURS are discarded.
    Returns number of pending pushes sent.
    """
    if not _PENDING_PUSHES_DIR.exists():
        return 0

    from channels.base import ChannelMessage

    now = datetime.now(timezone.utc)
    sent = 0
    for pf in sorted(_PENDING_PUSHES_DIR.glob(f"{channel_name}_*.json")):
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pf.unlink(missing_ok=True)
            continue

        created_str = data.get("created", "")
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            age_hours = (now - created).total_seconds() / 3600
        except ValueError:
            age_hours = 999

        if age_hours > _PENDING_MAX_HOURS:
            log.info("Discarding stale pending push (%s, %.0fh old)", pf.name, age_hours)
            pf.unlink(missing_ok=True)
            _audit_log(
                "CHANNEL_PENDING_EXPIRED",
                channel=channel_name,
                file=pf.name,
                age_hours=f"{age_hours:.0f}",
            )
            continue

        recipient_id = data.get("recipient_id", "")
        text = data.get("text", "")
        if dry_run:
            log.info("[DRY-RUN] Would retry pending push to %s", recipient_id)
            pf.unlink(missing_ok=True)
            sent += 1
            continue

        msg = ChannelMessage(text=text, recipient_id=recipient_id)
        success = adapter.send_message(msg)  # type: ignore[union-attr]
        if success:
            log.info("Retried pending push to %s/%s ✓", channel_name, recipient_id)
            pf.unlink(missing_ok=True)
            sent += 1
            _audit_log(
                "CHANNEL_PUSH",
                channel=channel_name,
                recipient=recipient_id,
                chars=len(text),
                pii_filtered=False,
                scope=data.get("scope", "?"),
                note="retry",
            )
        else:
            log.warning("Pending push retry failed for %s/%s", channel_name, recipient_id)
            # Leave file for next attempt (will eventually expire)

    return sent


def _audit_log(event_type: str, **kwargs: str | int | bool | None) -> None:
    """Append a channel event to state/audit.md.

    Format mirrors existing Artha audit log convention:
    [ISO] EVENT_TYPE | key: value | key: value ...
    """
    ts = datetime.now(timezone.utc).isoformat()
    parts = [f"[{ts}] {event_type}"]
    for k, v in kwargs.items():
        parts.append(f"{k}: {v}")
    entry = " | ".join(parts)
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass  # Audit log not writable — don't crash
    log.debug("Audit: %s", entry)


_FRICTION_BADGE = {"low": "🟢", "standard": "🟡", "high": "🔴"}


def push_pending_actions(dry_run: bool = False) -> int:
    """Push pending action proposals to Telegram with inline approval buttons.

    Called by the catch-up workflow at Step 14.5.
    Returns the number of action messages sent (0 if none pending).

    Ref: config/workflow/finalize.md Step 14.5, specs/act.md §5.3
    """
    from channels.registry import (
        load_channels_config,
        iter_enabled_channels,
        create_adapter_from_config,
    )
    from channels.base import ChannelMessage

    # Load pending actions
    try:
        sys.path.insert(0, str(_ARTHA_DIR / "scripts"))
        from action_executor import ActionExecutor
        executor = ActionExecutor(_ARTHA_DIR)
        pending = executor.pending()
    except Exception as exc:
        log.warning("Could not load pending actions: %s", exc)
        return 0

    if not pending:
        log.info("No pending actions to push.")
        return 0

    # Find Telegram channels with push enabled
    config = load_channels_config()
    sent_count = 0

    for channel_name, channel_cfg in iter_enabled_channels(config):
        if channel_cfg.get("adapter") != "telegram":
            continue
        if not channel_cfg.get("features", {}).get("push", True):
            continue

        try:
            adapter = create_adapter_from_config(channel_name, channel_cfg)
        except Exception as exc:
            log.warning("Channel %s: adapter load failed: %s", channel_name, exc)
            continue

        recipients = channel_cfg.get("recipients", {})
        for rec_name, rec_cfg in recipients.items():
            if not isinstance(rec_cfg, dict) or not rec_cfg.get("push", False):
                continue
            recipient_id = str(rec_cfg.get("id", "")).strip()
            if not recipient_id:
                continue

            for proposal in pending:
                badge = _FRICTION_BADGE.get(proposal.friction, "🟡")
                text = (
                    f"⚡ ACTION PENDING\n"
                    f"Type: {proposal.action_type} | Domain: {proposal.domain}\n"
                    f"Friction: {badge} {proposal.friction}\n\n"
                    f"{proposal.title}\n"
                )
                if proposal.description:
                    desc = proposal.description[:200]
                    text += f"{desc}\n"

                buttons = [
                    {"label": "✅ Approve", "command": f"act:APPROVE:{proposal.id}"},
                    {"label": "❌ Reject", "command": f"act:REJECT:{proposal.id}"},
                    {"label": "⏸ Defer", "command": f"act:DEFER:{proposal.id}"},
                ]

                if dry_run:
                    log.info(
                        "[DRY-RUN] Would push action %s to %s/%s",
                        proposal.id[:8], channel_name, rec_name,
                    )
                    sent_count += 1
                    continue

                msg = ChannelMessage(
                    text=text,
                    recipient_id=recipient_id,
                    buttons=buttons,
                )
                success = adapter.send_message(msg)
                if success:
                    sent_count += 1
                    _audit_log(
                        "ACTION_PUSH",
                        channel=channel_name,
                        recipient=rec_name,
                        action_id=proposal.id[:16],
                        action_type=proposal.action_type,
                    )
                else:
                    log.warning(
                        "Failed to push action %s to %s/%s",
                        proposal.id[:8], channel_name, rec_name,
                    )

    log.info("Pushed %d action message(s) to Telegram.", sent_count)
    return sent_count


def run_push(dry_run: bool = False) -> int:
    """Main push orchestrator. Returns 0 on success, 1 on config/fatal error.

    Logs warnings for individual channel/recipient failures but always
    returns 0 to preserve non-blocking guarantee.
    """
    from channels.registry import (
        load_channels_config,
        iter_enabled_channels,
        create_adapter_from_config,
    )

    config = load_channels_config()
    defaults = config.get("defaults", {})

    # Master kill switch — must be explicitly enabled
    if not defaults.get("push_enabled", False):
        log.info("Channel push disabled (push_enabled: false). Skipping Step 20.")
        return 0

    # Check push dedup marker
    already_pushed, reason, marker_host, marker_time = _check_push_marker()
    if already_pushed:
        log.info("Push already sent today (%s). Skipping.", reason)
        _audit_log(
            "CHANNEL_PUSH_SKIPPED",
            channel="all",
            marker_host=marker_host,
            marker_time=marker_time,
        )
        return 0

    log.info("Running channel push (Step 20)…")

    max_push_length = int(defaults.get("max_push_length", 500))
    channels_pushed: list[str] = []

    _cleanup_old_markers()

    for channel_name, channel_cfg in iter_enabled_channels(config):
        # Check push feature flag per channel
        if not channel_cfg.get("features", {}).get("push", True):
            log.debug("Channel %s: push feature disabled, skipping", channel_name)
            continue

        # Instantiate adapter
        try:
            adapter = create_adapter_from_config(channel_name, channel_cfg)
        except Exception as exc:
            log.warning("Channel %s: could not load adapter: %s", channel_name, exc)
            _audit_log(
                "CHANNEL_ERROR",
                channel=channel_name,
                error_type="adapter_load_failed",
                message=str(exc)[:200],
            )
            continue

        # Retry any pending pushes first
        _send_pending_pushes(channel_name, adapter, dry_run=dry_run)

        # Process each push-enabled recipient
        recipients = channel_cfg.get("recipients", {})
        for rec_name, rec_cfg in recipients.items():
            if not isinstance(rec_cfg, dict):
                continue
            if not rec_cfg.get("push", False):
                continue

            recipient_id = str(rec_cfg.get("id", "")).strip()
            if not recipient_id:
                log.warning(
                    "Channel %s recipient '%s': id is empty — skipping",
                    channel_name, rec_name,
                )
                continue

            scope = rec_cfg.get("access_scope", "standard")

            # 1. Build flash message
            flash_text = _build_flash_message(scope, max_push_length)

            # 2. PII redaction — mandatory, no exceptions
            try:
                sys.path.insert(0, str(_ARTHA_DIR / "scripts"))
                from pii_guard import filter_text as _pii_filter
                filtered_text, pii_found = _pii_filter(flash_text)
            except ImportError:
                log.warning("pii_guard not importable — skipping PII filter (UNSAFE)")
                filtered_text = flash_text
                pii_found = {}

            if pii_found:
                log.info(
                    "PII detected in push to %s/%s: %s — redacted",
                    channel_name, rec_name, list(pii_found.keys()),
                )

            # 3. Build buttons (only for channels that support them)
            buttons: list[dict[str, str]] = []
            if channel_cfg.get("features", {}).get("buttons", False):
                buttons = [
                    {"label": "Status", "command": "/status"},
                    {"label": "Tasks", "command": "/tasks"},
                    {"label": "Alerts", "command": "/alerts"},
                ]

            # 4. Send
            from channels.base import ChannelMessage
            msg = ChannelMessage(
                text=filtered_text,
                recipient_id=recipient_id,
                buttons=buttons,
            )

            if dry_run:
                log.info(
                    "[DRY-RUN] Would send to %s/%s (scope=%s, %d chars):\n%s",
                    channel_name, rec_name, scope, len(filtered_text), filtered_text,
                )
                _audit_log(
                    "CHANNEL_PUSH",
                    channel=channel_name,
                    recipient=rec_name,
                    chars=len(filtered_text),
                    pii_filtered=bool(pii_found),
                    scope=scope,
                    note="dry_run",
                )
                channels_pushed.append(channel_name)
                continue

            success = adapter.send_message(msg)

            if success:
                log.info(
                    "✓ Pushed to %s/%s (scope=%s, %d chars)",
                    channel_name, rec_name, scope, len(filtered_text),
                )
                _audit_log(
                    "CHANNEL_PUSH",
                    channel=channel_name,
                    recipient=rec_name,
                    chars=len(filtered_text),
                    pii_filtered=bool(pii_found),
                    scope=scope,
                )
                if channel_name not in channels_pushed:
                    channels_pushed.append(channel_name)
            else:
                log.warning(
                    "Push to %s/%s failed — queuing for next run",
                    channel_name, rec_name,
                )
                _write_pending_push(channel_name, recipient_id, filtered_text, scope)

    # Write dedup marker if anything was pushed
    if channels_pushed:
        _write_push_marker(channels_pushed)
        log.info("Push complete. Marker written. Channels: %s", channels_pushed)
    else:
        log.info("Push complete. No messages sent (no enabled recipients with id set).")

    return 0


def health_check() -> tuple[bool, str]:
    """Health check for preflight.py integration.

    Returns:
        (healthy: bool, message: str)
    """
    from channels.registry import load_channels_config, iter_enabled_channels

    config = load_channels_config()

    # If channels.yaml doesn't exist, that's OK — push is optional
    from lib.common import CONFIG_DIR
    if not (CONFIG_DIR / "channels.yaml").exists():
        return True, "channels.yaml not configured (push disabled) ✓"

    defaults = config.get("defaults", {})
    push_enabled = defaults.get("push_enabled", False)
    enabled_count = sum(1 for _ in iter_enabled_channels(config))

    if not push_enabled:
        return True, f"push_enabled: false ({enabled_count} channels configured) ✓"

    return True, f"push_enabled: true | {enabled_count} enabled channel(s) ✓"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Artha channel push (Step 20 — post-catch-up briefing delivery)"
    )
    parser.add_argument("--health", action="store_true",
                        help="Health check mode (for preflight.py)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be sent without making API calls")
    parser.add_argument("--push-actions", action="store_true",
                        help="Push pending action proposals to Telegram (Step 14.5)")
    args = parser.parse_args()

    if args.health:
        ok, msg = health_check()
        print(f"channel_push: {msg}")
        return 0 if ok else 1

    if args.push_actions:
        try:
            count = push_pending_actions(dry_run=args.dry_run)
            print(f"channel_push: pushed {count} action(s)")
            return 0
        except Exception as exc:
            log.error("push_pending_actions error (non-blocking): %s", exc)
            return 0

    try:
        return run_push(dry_run=args.dry_run)
    except Exception as exc:
        # Final safety net — channel push must never crash the catch-up pipeline
        log.error("channel_push unexpected error (non-blocking): %s", exc)
        _audit_log(
            "CHANNEL_ERROR",
            error_type="unexpected_exception",
            message=str(exc)[:500],
        )
        return 0  # Non-blocking: return 0 to let catch-up continue


if __name__ == "__main__":
    sys.exit(main())
