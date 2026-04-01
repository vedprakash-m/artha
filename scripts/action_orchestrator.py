#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module; PII guard applied to all action params
"""
scripts/action_orchestrator.py — Single CLI entry-point for the Artha Action Layer.

Wires together signal producers (email_signal_extractor, pattern_engine)
with action consumers (ActionComposer → ActionQueue → ActionExecutor) and
presents a numbered approval UX to the AI and user.

Usage
-----
    # Run signal extraction, compose proposals, print pending queue:
    python3 scripts/action_orchestrator.py --run
    python3 scripts/action_orchestrator.py --run --mcp          # skip email signals (pattern engine only)
    python3 scripts/action_orchestrator.py --run --verbose

    # Inspect / act on proposals:
    python3 scripts/action_orchestrator.py --list
    python3 scripts/action_orchestrator.py --show <action_id>
    python3 scripts/action_orchestrator.py --approve <action_id>
    python3 scripts/action_orchestrator.py --reject <action_id> [--reason "..."]
    python3 scripts/action_orchestrator.py --defer <action_id> [--until "+1h"|"tomorrow"|"next-session"]
    python3 scripts/action_orchestrator.py --approve-all-low
    python3 scripts/action_orchestrator.py --expire
    python3 scripts/action_orchestrator.py --health

Exit codes
----------
    0   All operations succeeded
    1   Partial failure (some proposals failed; others OK)
    2   Invalid arguments / config error
    3   Complete failure

Design
------
- Pure Python, no new dependencies
- All output: stdout for AI consumption; stderr for logging
- Non-blocking: never crashes the catch-up session
- Respects harness.actions.enabled kill switch
- Respects read-only mode via detect_environment.py
- Platform-local SQLite DB via ActionQueue._resolve_db_path()

Ref: specs/actions-reloaded.md v1.3.0
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: ensure scripts/ is on sys.path (mirrors pipeline.py pattern)
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Ensure correct venv before third-party imports (no-op if already in venv or CI)
try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Lazy imports — deferred so kill-switch check runs before any heavy work
# ---------------------------------------------------------------------------

def _import_action_modules() -> tuple[Any, Any, Any]:
    """Return (ActionComposer, ActionExecutor, EmailSignalExtractor)."""
    from action_composer import ActionComposer  # type: ignore[import]
    from action_executor import ActionExecutor  # type: ignore[import]
    from email_signal_extractor import EmailSignalExtractor  # type: ignore[import]
    return ActionComposer, ActionExecutor, EmailSignalExtractor


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_artha_config(artha_dir: Path) -> dict[str, Any]:
    """Load config/artha_config.yaml. Returns empty dict on failure."""
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        return load_config("artha_config", str(artha_dir / "config")) or {}
    except Exception:
        pass
    return {}


def _nested_get(d: dict, *keys: str, default: Any = None) -> Any:
    """Navigate nested dict with dot-style keys. Returns default on missing."""
    node: Any = d
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, default)
    return node


def _actions_enabled(artha_dir: Path) -> bool:
    """Check harness.actions.enabled in artha_config.yaml (default: True)."""
    cfg = _load_artha_config(artha_dir)
    return bool(_nested_get(cfg, "harness", "actions", "enabled", default=True))


def _burn_in_mode(artha_dir: Path) -> bool:
    """Check harness.actions.burn_in in artha_config.yaml (default: False)."""
    cfg = _load_artha_config(artha_dir)
    return bool(_nested_get(cfg, "harness", "actions", "burn_in", default=False))


def _ai_signals_enabled(artha_dir: Path) -> bool:
    """Check harness.actions.ai_signals in artha_config.yaml (default: False).

    AI-emitted signals are default-off (V1.1) pending security burn-in.
    Ref: specs/actions-reloaded.md §SP-4
    """
    cfg = _load_artha_config(artha_dir)
    return bool(_nested_get(cfg, "harness", "actions", "ai_signals", default=False))


def _is_read_only(artha_dir: Path) -> bool:
    """Check if running in read-only mode via detect_environment.py."""
    try:
        from detect_environment import probe_environment  # type: ignore[import]
        manifest = probe_environment()
        return not manifest.capabilities.get("filesystem_writable", True)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Signal loading helpers
# ---------------------------------------------------------------------------

def _load_emails(path: Path) -> list[dict]:
    """Load email records from pipeline JSONL, excluding marketing."""
    emails: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                    if record.get("marketing"):
                        continue
                    emails.append(record)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return emails


# Required fields for a valid AI-emitted signal (schema validation, §SP-4 hardening step 3)
_AI_SIGNAL_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"signal_type", "domain", "entity", "urgency", "impact", "source"}
)
# AI signals MUST identify themselves as such (§SP-4 hardening step 1)
_AI_SIGNAL_SOURCE_VALUE = "ai"


def _load_ai_signals(path: Path) -> list[Any]:
    """Load and validate AI-emitted signals from tmp/ai_signals.jsonl.

    Security hardening (§SP-4, §7.2.1):
    1. Mandatory source field — must be "ai" (not spoofable as email_extractor/pattern_engine)
    2. Schema validation — required fields enforced; malformed lines silently skipped
    3. Unknown signal types pass through — routing table drops them during compose()
    4. Friction escalation applied downstream by _apply_ai_signal_hardening()

    Skips silently on any parse or IO error — AI signals are never blocking.
    """
    from types import SimpleNamespace

    signals: list[Any] = []
    skipped = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    print(
                        f"[action_orchestrator] ai_signals line {i}: invalid JSON — skipped",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue

                if not isinstance(record, dict):
                    skipped += 1
                    continue

                # Validate required fields (hardening step 3 — schema validation)
                missing_fields = _AI_SIGNAL_REQUIRED_FIELDS - set(record.keys())
                if missing_fields:
                    print(
                        f"[action_orchestrator] ai_signals line {i}: missing fields "
                        f"{sorted(missing_fields)} — skipped",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue

                # Enforce mandatory source tag (hardening step 1)
                # Reject attempts to impersonate deterministic signal sources
                if record.get("source") != _AI_SIGNAL_SOURCE_VALUE:
                    print(
                        f"[action_orchestrator] ai_signals line {i}: source must be "
                        f"'{_AI_SIGNAL_SOURCE_VALUE}', got '{record.get('source')}' — skipped",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue

                signals.append(SimpleNamespace(
                    signal_type=str(record["signal_type"]),
                    domain=str(record["domain"]),
                    entity=str(record["entity"]),
                    urgency=int(record.get("urgency", 5)),
                    impact=int(record.get("impact", 5)),
                    source=_AI_SIGNAL_SOURCE_VALUE,
                    detected_at=record.get("detected_at", ""),
                    metadata={},  # AI-emitted metadata never trusted
                    _ai_origin=True,  # Internal tag for friction escalation
                ))
    except OSError:
        pass

    if skipped:
        print(
            f"[action_orchestrator] ai_signals: {skipped} line(s) skipped (schema/source validation)",
            file=sys.stderr,
        )
    return signals


def _apply_ai_signal_hardening(proposal: Any) -> Any:
    """Escalate friction to 'high' for all AI-originated proposals.

    Security hardening step 2 (§SP-4, §7.2.1): AI signals must never produce
    low/standard friction proposals — batch-approve via --approve-all-low must
    not be usable for AI-originated actions. Human review is always required.

    Returns a new ActionProposal with friction='high' using dataclasses.replace().
    """
    import dataclasses
    if getattr(proposal, "friction", "high") != "high":
        proposal = dataclasses.replace(proposal, friction="high")
    return proposal


def _deduplicate(signals: list[Any]) -> list[Any]:
    """Remove duplicate signals by (signal_type, domain, entity) within the same run.

    Cross-session dedup is handled by ActionQueue.propose() status-based guard.
    Ref: specs/actions-reloaded.md §WB-3
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[Any] = []
    for s in signals:
        key = (
            getattr(s, "signal_type", ""),
            getattr(s, "domain", ""),
            getattr(s, "entity", ""),
        )
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def _persist_signals(path: Path, signals: list[Any]) -> None:
    """Write signal envelope (no PII metadata) to JSONL for auditability.

    Intentionally excludes signal.metadata to prevent PII leakage.
    Ref: specs/actions-reloaded.md §WB-4
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for s in signals:
                record = {
                    "signal_type": getattr(s, "signal_type", ""),
                    "domain": getattr(s, "domain", ""),
                    "entity": getattr(s, "entity", ""),
                    "urgency": getattr(s, "urgency", 0),
                    "impact": getattr(s, "impact", 0),
                    "source": getattr(s, "source", ""),
                    "detected_at": getattr(s, "detected_at", ""),
                    # metadata intentionally excluded — may contain PII
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[action_orchestrator] signal persistence failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Handler health check (RH-1: import-level, fast startup check)
# ---------------------------------------------------------------------------

def _handler_health_check(artha_dir: Path) -> list[str]:
    """Verify all handler modules import cleanly. Returns list of failure strings.

    Import-level check only (~10ms) — deliberately avoids executing
    handler.health_check() which may make network calls.
    Full functional health is available via --health / executor.run_health_checks().
    Ref: specs/actions-reloaded.md §RH-1
    """
    try:
        from actions import _HANDLER_MAP  # type: ignore[import]
        handler_map = _HANDLER_MAP
    except ImportError:
        # Fallback: get map from executor's fallback
        try:
            from action_executor import _FALLBACK_ACTION_MAP  # type: ignore[import]
            handler_map = _FALLBACK_ACTION_MAP
        except ImportError:
            return ["actions._HANDLER_MAP: import failed (scripts/ path error)"]

    failures: list[str] = []
    for action_type, module_path in handler_map.items():
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            failures.append(f"{action_type} ({module_path}): {exc}")
    return failures


# ---------------------------------------------------------------------------
# Pre-enqueue handler validation (WB-1 _validate_proposal_handler)
# ---------------------------------------------------------------------------

def _validate_proposal_handler(executor: Any, proposal: Any) -> None:
    """Run handler.validate() before enqueue to catch structural issues early.

    Raises ValueError if the proposal would fail at execution time.
    Catches issues like email_send with no 'to' field before the user
    ever approves.
    Ref: specs/actions-reloaded.md §WB-1 _validate_proposal_handler
    """
    handler = executor._get_handler(proposal.action_type)
    ok, reason = handler.validate(proposal)
    if not ok:
        raise ValueError(f"Handler pre-validation failed: {reason}")


# ---------------------------------------------------------------------------
# Defer preset resolution (F-5: must resolve before calling executor.defer)
# ---------------------------------------------------------------------------

def _resolve_defer_preset(preset: str) -> str:
    """Translate human-friendly defer presets to ISO-8601 UTC strings.

    The executor's _resolve_defer_time() only understands '+Nh'/'+Nm' offsets
    and ISO strings — it does NOT understand 'tomorrow' or 'next-session'.
    This function is the translation layer.

    Supported presets:
        +1h, +4h, +Xh/+Xm  → N hours/minutes from now
        tomorrow            → next calendar day at 09:00 local time
        next-session        → +24h (default when bare 'defer' is used)

    Ref: specs/actions-reloaded.md §WB-1 _resolve_defer_preset
    """
    preset = preset.strip().lower()

    if preset in ("next-session", ""):
        # Default: 24h from now
        dt = datetime.now(timezone.utc) + timedelta(hours=24)
        return dt.isoformat(timespec="seconds")

    if preset == "tomorrow":
        # Next day at 09:00 local time, expressed as a UTC ISO string
        import time as _time
        local_now = datetime.now()
        tomorrow_local = (local_now + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        # Get UTC offset
        utc_offset_sec = -_time.timezone if not _time.daylight else -_time.altzone
        utc_offset = timedelta(seconds=utc_offset_sec)
        tomorrow_utc = tomorrow_local - utc_offset
        tomorrow_utc = tomorrow_utc.replace(tzinfo=timezone.utc)
        return tomorrow_utc.isoformat(timespec="seconds")

    if preset.startswith("+") and preset[1:].rstrip("hm").isdigit():
        # Offset: +Nh or +Nm
        raw = preset[1:]
        if raw.endswith("h"):
            hours = int(raw[:-1])
            dt = datetime.now(timezone.utc) + timedelta(hours=hours)
        elif raw.endswith("m"):
            minutes = int(raw[:-1])
            dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        else:
            hours = int(raw)
            dt = datetime.now(timezone.utc) + timedelta(hours=hours)
        return dt.isoformat(timespec="seconds")

    # Pass through as-is (may be an ISO string already)
    return preset


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _audit_log(artha_dir: Path, message: str) -> None:
    """Append a timestamped line to state/audit.md."""
    try:
        audit_path = artha_dir / "state" / "audit.md"
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"[{ts}] {message}\n"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass  # Audit logging is best-effort — never blocks operation


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

_FRICTION_ICON = {"high": "🔴", "standard": "🟠", "low": "🟢"}
_CONTENT_BEARING_TYPES = frozenset({"email_send", "email_reply", "whatsapp_send"})

# RH-5: wall-clock timeout for the entire --run operation.
# Set via _run_with_timeout(); not applied to other CLI commands.
# UNIX/macOS only (SIGALRM unavailable on Windows — operation is deterministic
# regex + YAML so a runaway is implausible).
_RUN_TIMEOUT_SEC = 60


class _OrchestratorTimeout(BaseException):
    """Raised by SIGALRM when --run exceeds _RUN_TIMEOUT_SEC (RH-5).

    Extends BaseException (not Exception) so it propagates through bare
    'except Exception:' guards inside run() without being silently swallowed.
    """


def _print_summary(
    all_signals: list[Any],
    email_signal_count: int,
    pattern_signal_count: int,
    proposed: int,
    suppressed: int,
    expired: int,
    pending: list[Any],
    burn_in: bool = False,
    verbose: bool = False,
) -> None:
    """Print structured summary to stdout for AI consumption.

    Format matches specs/actions-reloaded.md §WB-1 output spec.
    """
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    depth = len(pending)
    errors = 0  # counted by caller

    prefix = "[BURN-IN] " if burn_in else ""

    print(f"═══ {prefix}ACTION ORCHESTRATOR ═══════════════════════════════════")
    print(
        f"Signals detected: {len(all_signals)} "
        f"(email: {email_signal_count}, pattern: {pattern_signal_count})"
    )
    print(f"Proposals queued: {proposed} ({suppressed} duplicate suppressed)")
    print(f"Expired: {expired}")

    # Emit structured counter line to stdout for machine parsing
    print(
        f"\n[{ts}] ACTION_ORCHESTRATOR | "
        f"signals:{len(all_signals)} suppressed:{suppressed} "
        f"queued:{proposed} expired:{expired} depth:{depth} errors:{errors}"
    )

    if pending:
        cap = 10
        displayed = pending[:cap]
        print(f"\n─── PENDING ACTIONS ({depth}) ───────────────────────────────────────")
        for i, p in enumerate(displayed, 1):
            icon = _FRICTION_ICON.get(getattr(p, "friction", "standard"), "🟠")
            action_type = getattr(p, "action_type", "")
            domain = getattr(p, "domain", "")
            title = getattr(p, "title", "")
            pid = getattr(p, "id", "")[:8]
            friction = getattr(p, "friction", "standard")
            min_trust = getattr(p, "min_trust", 1)
            expires_at = getattr(p, "expires_at", "")
            content_flag = " [content]" if action_type in _CONTENT_BEARING_TYPES else ""
            print(
                f"{i}. [{pid}] {icon} {action_type} | {domain} | {title[:60]}{content_flag}"
            )
            print(
                f"   Friction: {friction} | Trust: {min_trust} | Expires: {expires_at}"
            )
        if depth > cap:
            extra = depth - cap
            print(f"... and {extra} more. Run 'items' or "
                  f"'python3 scripts/action_orchestrator.py --list' to see all.")

        print(
            "\nCommands: approve <id>, reject <id>, "
            "approve-all-low, defer <id> [--until \"+1h\"|\"tomorrow\"|\"next-session\"]"
        )
    else:
        print("\n(No pending actions)")

    print("════════════════════════════════════════════════════════════════")


def _print_expanded_preview(proposal: Any) -> None:
    """Print full detail view of a single proposal for content review.

    Ref: specs/actions-reloaded.md §WB-1 --show output format
    """
    action_type = getattr(proposal, "action_type", "")
    domain = getattr(proposal, "domain", "")
    friction = getattr(proposal, "friction", "standard")
    min_trust = getattr(proposal, "min_trust", 1)
    expires_at = getattr(proposal, "expires_at", "")
    title = getattr(proposal, "title", "")
    pid = getattr(proposal, "id", "")
    params = getattr(proposal, "parameters", {})

    print("═══ ACTION DETAIL ══════════════════════════════════════════════")
    print(f"ID:       {pid}")
    print(f"Type:     {action_type}")
    print(f"Domain:   {domain}")
    print(f"Friction: {friction}")
    print(f"Trust:    {min_trust}")
    print(f"Expires:  {expires_at}")
    print()
    print(f"Title:    {title}")

    if action_type in _CONTENT_BEARING_TYPES:
        if isinstance(params, dict):
            print()
            print("─── CONTENT PREVIEW ──────────────────────────────────────────")
            if "to" in params:
                print(f"To:       {params['to']}")
            if "subject" in params:
                print(f"Subject:  {params['subject']}")
            if "body" in params:
                body_lines = str(params["body"]).splitlines()
                cap = 80
                print("Body:")
                for line in body_lines[:cap]:
                    print(f"  {line}")
                if len(body_lines) > cap:
                    print(f"  ... (truncated, {len(body_lines) - cap} more lines)")
        else:
            print("🔒 Parameters encrypted — decrypt from Mac terminal to preview.")
    else:
        if isinstance(params, dict):
            print()
            print("─── PARAMETERS ───────────────────────────────────────────────")
            for k, v in list(params.items())[:10]:
                print(f"  {k}: {str(v)[:120]}")

    print("════════════════════════════════════════════════════════════════")


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def run(
    artha_dir: Path,
    mcp_mode: bool = False,
    verbose: bool = False,
) -> int:
    """Signal extraction → composition → queue. Returns proposal count.

    Ref: specs/actions-reloaded.md §WB-1 run() implementation
    """
    # Guard: actions enabled?
    if not _actions_enabled(artha_dir):
        print("[action_orchestrator] actions disabled — skipping", file=sys.stderr)
        return 0

    # Guard: read-only mode?
    if _is_read_only(artha_dir):
        print("[action_orchestrator] read-only mode — skipping", file=sys.stderr)
        return 0

    burn_in = _burn_in_mode(artha_dir)

    # RH-1: Import-level handler health check at startup
    handler_failures = _handler_health_check(artha_dir)
    unavailable_types: set[str] = set()
    if handler_failures:
        print(
            f"[action_orchestrator] {len(handler_failures)} handler(s) failed import check:",
            file=sys.stderr,
        )
        for f in handler_failures:
            print(f"  ✗ {f}", file=sys.stderr)
            # Extract action type from "action_type (module): error" format
            action_type = f.split(" (")[0].strip()
            unavailable_types.add(action_type)
        _audit_log(
            artha_dir,
            f"ACTION_HANDLER_CHECK | failures:{len(handler_failures)} | "
            f"types:{','.join(sorted(unavailable_types))}",
        )

    ActionComposer, ActionExecutor, EmailSignalExtractor = _import_action_modules()

    # 1. Collect signals from email extractor (skip in --mcp mode)
    email_signals: list[Any] = []
    emails_path = artha_dir / "tmp" / "pipeline_output.jsonl"

    if mcp_mode:
        if verbose:
            print("[action_orchestrator] --mcp: skipping email signal extraction", file=sys.stderr)
    elif emails_path.exists():
        emails = _load_emails(emails_path)
        if emails:
            try:
                extractor = EmailSignalExtractor()
                email_signals = extractor.extract(emails)
                if verbose:
                    print(
                        f"[action_orchestrator] email signals: {len(email_signals)} from {len(emails)} emails",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(f"[action_orchestrator] email extractor error: {exc}", file=sys.stderr)
        else:
            if verbose:
                print("[action_orchestrator] email JSONL found but 0 non-marketing records", file=sys.stderr)
    else:
        # RH-2: Graceful degradation on missing pipeline file
        print(
            "[action_orchestrator] no pipeline output found — "
            "email signals: 0 (no pipeline data). Use --mcp if running Tier 1.",
            file=sys.stderr,
        )

    # 2. Collect signals from pattern engine
    pattern_signals: list[Any] = []
    try:
        from pattern_engine import PatternEngine  # type: ignore[import]
        engine = PatternEngine(root_dir=artha_dir)
        pattern_signals = engine.evaluate()
        if verbose:
            print(f"[action_orchestrator] pattern signals: {len(pattern_signals)}", file=sys.stderr)
    except Exception as exc:
        print(f"[action_orchestrator] pattern engine error: {exc}", file=sys.stderr)

    # 3. Load AI-emitted signals (default-off; gated by harness.actions.ai_signals)
    # Hardening: schema validation, source enforcement, friction escalation applied here.
    # Ref: specs/actions-reloaded.md §SP-4, §7.2.1
    ai_signals: list[Any] = []
    if _ai_signals_enabled(artha_dir):
        ai_signal_path = artha_dir / "tmp" / "ai_signals.jsonl"
        if ai_signal_path.exists():
            ai_signals = _load_ai_signals(ai_signal_path)
            if verbose:
                print(
                    f"[action_orchestrator] ai signals: {len(ai_signals)} loaded",
                    file=sys.stderr,
                )
        elif verbose:
            print("[action_orchestrator] ai_signals enabled but tmp/ai_signals.jsonl absent", file=sys.stderr)
    else:
        if verbose:
            print("[action_orchestrator] ai signals disabled (harness.actions.ai_signals=false)", file=sys.stderr)

    # 4. Merge + deduplicate (email + pattern + AI)
    all_signals = _deduplicate(email_signals + pattern_signals + ai_signals)
    suppressed = (len(email_signals) + len(pattern_signals) + len(ai_signals)) - len(all_signals)

    if verbose:
        print(
            f"[action_orchestrator] total signals: {len(all_signals)} "
            f"({suppressed} within-run duplicates suppressed)",
            file=sys.stderr,
        )

    # 5. Persist signals for audit (envelope only — no PII metadata)
    signals_path = artha_dir / "tmp" / "signals.jsonl"
    _persist_signals(signals_path, all_signals)

    # 6. Compose → propose
    composer = ActionComposer(artha_dir=artha_dir)
    executor = ActionExecutor(artha_dir)
    proposed = 0

    for signal in all_signals:
        signal_type = getattr(signal, "signal_type", "unknown")
        is_ai_signal = getattr(signal, "_ai_origin", False)
        action_type_target = ""

        # Skip signals whose target action_type's handler failed import
        try:
            from action_composer import _load_signal_routing  # type: ignore[import]
            route = _load_signal_routing().get(signal_type, {})
            action_type_target = route.get("action_type", "")
        except Exception:
            pass

        if action_type_target in unavailable_types:
            print(
                f"[action_orchestrator] skipping {signal_type} — handler unavailable for {action_type_target}",
                file=sys.stderr,
            )
            continue

        try:
            proposal = composer.compose(signal)
            if proposal is None:
                continue

            # Hardening step 2: Escalate friction to 'high' for all AI-origin proposals.
            # Prevents batch-approval via --approve-all-low for AI-generated actions.
            # Ref: specs/actions-reloaded.md §SP-4
            if is_ai_signal:
                proposal = _apply_ai_signal_hardening(proposal)

            # Pre-enqueue handler validation (catches structural issues before user sees proposal)
            try:
                _validate_proposal_handler(executor, proposal)
            except ValueError as ve:
                print(
                    f"[action_orchestrator] handler validation failed for {signal_type}: {ve}",
                    file=sys.stderr,
                )
                continue

            executor.propose_direct(proposal)
            proposed += 1

            # Hardening step 4: AI-signal proposals get [AI-SIGNAL] audit prefix for
            # easy filtering during burn-in review.
            # Ref: specs/actions-reloaded.md §SP-4
            ai_tag = "[AI-SIGNAL] " if is_ai_signal else ""
            _audit_log(
                artha_dir,
                f"{ai_tag}ACTION_PROPOSED | id:{proposal.id} | type:{proposal.action_type} "
                f"| domain:{proposal.domain} | friction:{proposal.friction}",
            )

        except Exception as exc:
            # compose/propose loop must never crash the session (Principle 7)
            print(
                f"[action_orchestrator] compose/propose failed for {signal_type}: {exc}",
                file=sys.stderr,
            )

    # 7. Expire stale proposals
    expired = 0
    try:
        expired = executor.expire_stale()
    except Exception as exc:
        print(f"[action_orchestrator] expire_stale failed: {exc}", file=sys.stderr)

    # 8. Fetch pending for display
    pending: list[Any] = []
    try:
        pending = executor.list_pending()
    except Exception as exc:
        print(f"[action_orchestrator] list_pending failed: {exc}", file=sys.stderr)

    # 9. Audit the run
    _audit_log(
        artha_dir,
        f"ACTION_ORCHESTRATOR | signals:{len(all_signals)} suppressed:{suppressed} "
        f"queued:{proposed} expired:{expired} depth:{len(pending)} errors:0",
    )

    # 10. Print summary for AI consumption
    _print_summary(
        all_signals=all_signals,
        email_signal_count=len(email_signals),
        pattern_signal_count=len(pattern_signals),
        proposed=proposed,
        suppressed=suppressed,
        expired=expired,
        pending=pending,
        burn_in=burn_in,
        verbose=verbose,
    )

    executor.close()
    return proposed


def _run_with_timeout(
    artha_dir: Path,
    mcp_mode: bool = False,
    verbose: bool = False,
    timeout_sec: int = _RUN_TIMEOUT_SEC,
) -> int:
    """Invoke run() with a SIGALRM wall-clock timeout (RH-5).

    On timeout: logs ACTION_ORCHESTRATOR_TIMEOUT to audit.md, prints a warning
    to stderr, and returns 0 (proposals already committed to SQLite remain valid).

    Windows: SIGALRM is unavailable — run() is called without a timeout wrapper.
    The operation is deterministic regex + YAML so a runaway is implausible.

    Ref: specs/actions-reloaded.md §RH-5
    """
    import signal as _sig  # local import avoids shadowing 'signal' loop var in run()

    if not hasattr(_sig, "SIGALRM"):
        # Windows / platforms without SIGALRM — skip timeout enforcement
        return run(artha_dir, mcp_mode=mcp_mode, verbose=verbose)

    def _alarm_handler(signum: int, frame: Any) -> None:  # noqa: ANN001
        raise _OrchestratorTimeout()

    prev_handler = _sig.signal(_sig.SIGALRM, _alarm_handler)
    _sig.alarm(timeout_sec)
    try:
        return run(artha_dir, mcp_mode=mcp_mode, verbose=verbose)
    except _OrchestratorTimeout:
        print(
            f"[action_orchestrator] --run timed out after {timeout_sec}s "
            "— partial results remain in DB",
            file=sys.stderr,
        )
        _audit_log(
            artha_dir,
            f"ACTION_ORCHESTRATOR_TIMEOUT | limit_sec:{timeout_sec}",
        )
        return 0  # proposals committed before timeout remain in DB
    finally:
        _sig.alarm(0)  # cancel any pending alarm
        _sig.signal(_sig.SIGALRM, prev_handler)  # restore original handler


def cmd_list(artha_dir: Path) -> int:
    """Print all pending proposals in numbered format."""
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        pending = executor.list_pending()
        if not pending:
            print("(No pending actions)")
            return 0
        print(f"═══ PENDING ACTIONS ({len(pending)}) ═══════════════════════════════════")
        for i, p in enumerate(pending, 1):
            icon = _FRICTION_ICON.get(getattr(p, "friction", "standard"), "🟠")
            action_type = getattr(p, "action_type", "")
            domain = getattr(p, "domain", "")
            title = getattr(p, "title", "")
            pid = getattr(p, "id", "")[:8]
            friction = getattr(p, "friction", "standard")
            min_trust = getattr(p, "min_trust", 1)
            expires_at = getattr(p, "expires_at", "")
            content_flag = " [content]" if action_type in _CONTENT_BEARING_TYPES else ""
            print(f"{i}. [{pid}] {icon} {action_type} | {domain} | {title[:60]}{content_flag}")
            print(f"   Friction: {friction} | Trust: {min_trust} | Expires: {expires_at}")
        print("════════════════════════════════════════════════════════════════")
        return 0
    finally:
        executor.close()


def cmd_show(artha_dir: Path, action_id: str) -> int:
    """Display full content preview for a proposal (required for content-bearing actions).

    Ref: specs/actions-reloaded.md §WB-1 --show behaviour
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        proposal = executor.get_action(action_id)
        if not proposal:
            # Try prefix match (user may have given the 8-char prefix)
            if len(action_id) < 36:
                try:
                    full_id = _resolve_id(executor, action_id)
                    proposal = executor.get_action(full_id)
                except ValueError as e:
                    print(f"[action] {e}", file=sys.stderr)
                    return 1
            if not proposal:
                print(f"[action] Action '{action_id}' not found.", file=sys.stderr)
                return 1
        _print_expanded_preview(proposal)
        return 0
    finally:
        executor.close()


def cmd_approve(artha_dir: Path, action_id: str) -> int:
    """Approve and execute a pending proposal.

    Ref: specs/actions-reloaded.md §WB-1 --approve behaviour
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        # Resolve prefix to full ID if needed
        action_id = _resolve_id(executor, action_id)
        result = executor.approve(action_id, approved_by="user:terminal")
        status = getattr(result, "status", "unknown")
        message = getattr(result, "message", "")
        if status == "success":
            print(f"[action] ✓ {status}: {message}")
            _audit_log(artha_dir, f"ACTION_APPROVED | id:{action_id} | by:user:terminal")
            _audit_log(artha_dir, f"ACTION_EXECUTED | id:{action_id} | status:success | msg:{message!r}")
            return 0
        else:
            print(f"[action] ✗ {status}: {message}")
            _audit_log(artha_dir, f"ACTION_EXECUTED | id:{action_id} | status:failure | msg:{message!r}")
            return 1
    except Exception as exc:
        print(f"[action] ✗ approve failed: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()


def cmd_reject(artha_dir: Path, action_id: str, reason: str = "") -> int:
    """Reject a pending proposal."""
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        action_id = _resolve_id(executor, action_id)
        executor.reject(action_id, reason=reason)
        print(f"[action] ✓ rejected: {action_id[:8]}")
        _audit_log(artha_dir, f"ACTION_REJECTED | id:{action_id} | reason:{reason!r}")
        return 0
    except Exception as exc:
        print(f"[action] ✗ reject failed: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()


def cmd_defer(artha_dir: Path, action_id: str, until: str = "next-session") -> int:
    """Defer a pending proposal to a later time.

    The until preset is resolved to an ISO-8601 UTC string before calling
    executor.defer(), which only understands +Nh offsets and ISO strings.
    Ref: specs/actions-reloaded.md §WB-1 --defer behaviour
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        action_id = _resolve_id(executor, action_id)
        resolved_until = _resolve_defer_preset(until)
        executor.defer(action_id, until=resolved_until)
        print(f"[action] ✓ deferred to {resolved_until}: {action_id[:8]}")
        _audit_log(artha_dir, f"ACTION_DEFERRED | id:{action_id} | until:{resolved_until}")
        return 0
    except Exception as exc:
        print(f"[action] ✗ defer failed: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()


def cmd_approve_all_low(artha_dir: Path) -> int:
    """Approve all low-friction pending proposals. High/standard friction untouched.

    Ref: specs/actions-reloaded.md §T-U-19
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        pending = executor.list_pending()
        low_friction = [p for p in pending if getattr(p, "friction", "standard") == "low"]
        if not low_friction:
            print("[action] No low-friction proposals pending.")
            return 0
        approved = 0
        failed = 0
        for p in low_friction:
            pid = getattr(p, "id", "")
            try:
                result = executor.approve(pid, approved_by="user:terminal")
                status = getattr(result, "status", "unknown")
                if status == "success":
                    print(f"[action] ✓ approved {pid[:8]}: {getattr(p, 'title', '')[:60]}")
                    approved += 1
                else:
                    print(f"[action] ✗ {pid[:8]}: {getattr(result, 'message', '')}")
                    failed += 1
            except Exception as exc:
                print(f"[action] ✗ {pid[:8]}: {exc}", file=sys.stderr)
                failed += 1
        print(f"[action] approve-all-low: {approved} approved, {failed} failed, "
              f"{len(pending) - len(low_friction)} high/standard skipped")
        return 0 if failed == 0 else 1
    finally:
        executor.close()


def cmd_expire(artha_dir: Path) -> int:
    """Sweep expired proposals from the queue."""
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        count = executor.expire_stale()
        print(f"[action] expired {count} stale proposal(s)")
        if count:
            _audit_log(artha_dir, f"ACTION_EXPIRY_SWEEP | expired:{count}")
        return 0
    finally:
        executor.close()


def cmd_health(artha_dir: Path) -> int:
    """Print action layer health status.

    Ref: specs/actions-reloaded.md §E.2 --health extended output
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        # Queue stats
        try:
            stats = executor.queue_stats()
        except Exception:
            stats = {}

        # Handler health (full functional check)
        try:
            handler_results = executor.run_health_checks()
        except Exception:
            handler_results = {}

        healthy_handlers = sum(1 for ok in handler_results.values() if ok)
        total_handlers = len(handler_results)

        # Config flags
        cfg = _load_artha_config(artha_dir)
        actions_enabled = _nested_get(cfg, "harness", "actions", "enabled", default=True)
        ai_signals = _nested_get(cfg, "harness", "actions", "ai_signals", default=False)
        burn_in = _nested_get(cfg, "harness", "actions", "burn_in", default=False)

        # DB path
        from action_queue import ActionQueue  # type: ignore[import]
        db_path = ActionQueue._resolve_db_path(artha_dir)

        print("═══ ACTION LAYER HEALTH ════════════════════════════════════════")
        pending_count = stats.get("pending", 0)
        deferred_count = stats.get("deferred", 0)
        print(f"Queue: {pending_count} pending, {deferred_count} deferred")

        if handler_results:
            unhealthy = [t for t, ok in handler_results.items() if not ok]
            health_str = f"{healthy_handlers}/{total_handlers} healthy"
            if unhealthy:
                health_str += f" ({', '.join(unhealthy)}: unavailable)"
            print(f"Handlers: {health_str}")
        else:
            print("Handlers: (no data)")

        print(
            f"Config: actions.enabled={actions_enabled}, "
            f"ai_signals={ai_signals}, burn_in={burn_in}"
        )
        print(f"DB: {db_path}")
        print("════════════════════════════════════════════════════════════════")
        return 0
    finally:
        executor.close()


# ---------------------------------------------------------------------------
# ID resolution helper (prefix → full UUID)
# ---------------------------------------------------------------------------

def _resolve_id(executor: Any, id_or_prefix: str) -> str:
    """Resolve a short (8-char) ID prefix to a full UUID.

    If the prefix is already a full UUID (36 chars), return as-is.
    Raises ValueError if the prefix is ambiguous or not found.
    """
    if len(id_or_prefix) == 36:
        return id_or_prefix  # already a full UUID

    pending = executor.list_pending()
    matches = [p for p in pending if getattr(p, "id", "").startswith(id_or_prefix)]

    if not matches:
        # Not in pending — check deferred and other non-terminal statuses
        matches = executor.queue.find_by_prefix(id_or_prefix)

    if not matches:
        raise ValueError(
            f"Action prefix '{id_or_prefix}' not found in any active action."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous prefix '{id_or_prefix}' — {len(matches)} proposals match. "
            "Use a longer prefix or the full ID."
        )
    return getattr(matches[0], "id", id_or_prefix)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="action_orchestrator.py",
        description="Artha action layer CLI — signal extraction, proposal management.",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run",
        action="store_true",
        help="Extract signals, compose proposals, print pending queue",
    )
    group.add_argument(
        "--list",
        action="store_true",
        dest="list_pending",
        help="List all pending proposals",
    )
    group.add_argument(
        "--show",
        metavar="ID",
        help="Show expanded preview of a proposal",
    )
    group.add_argument(
        "--approve",
        metavar="ID",
        help="Approve and execute a proposal",
    )
    group.add_argument(
        "--reject",
        metavar="ID",
        help="Reject a proposal",
    )
    group.add_argument(
        "--defer",
        metavar="ID",
        help="Defer a proposal to a later time",
    )
    group.add_argument(
        "--approve-all-low",
        action="store_true",
        help="Approve all low-friction proposals",
    )
    group.add_argument(
        "--expire",
        action="store_true",
        help="Sweep expired proposals",
    )
    group.add_argument(
        "--health",
        action="store_true",
        help="Print action layer health status",
    )

    p.add_argument(
        "--mcp",
        action="store_true",
        help="MCP Tier 1: skip email signal extraction (pattern engine only)",
    )
    p.add_argument(
        "--reason",
        default="",
        help="Reason for rejection (used with --reject)",
    )
    p.add_argument(
        "--until",
        default="next-session",
        help="Defer horizon: +1h, +4h, tomorrow, next-session (default: next-session)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging to stderr",
    )
    p.add_argument(
        "--artha-dir",
        metavar="PATH",
        default=str(_ARTHA_DIR),
        help="Artha workspace root (default: parent of scripts/)",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    artha_dir = Path(args.artha_dir).resolve()

    if args.run:
        # RH-5: wrapped with wall-clock timeout (UNIX only)
        proposed = _run_with_timeout(artha_dir, mcp_mode=args.mcp, verbose=args.verbose)
        return 0 if proposed >= 0 else 3

    if args.list_pending:
        return cmd_list(artha_dir)

    if args.show:
        return cmd_show(artha_dir, args.show)

    if args.approve:
        return cmd_approve(artha_dir, args.approve)

    if args.reject:
        return cmd_reject(artha_dir, args.reject, reason=args.reason)

    if args.defer:
        return cmd_defer(artha_dir, args.defer, until=args.until)

    if args.approve_all_low:
        return cmd_approve_all_low(artha_dir)

    if args.expire:
        return cmd_expire(artha_dir)

    if args.health:
        return cmd_health(artha_dir)

    return 2  # unreachable — argparse enforces mutual exclusion


if __name__ == "__main__":
    sys.exit(main())
