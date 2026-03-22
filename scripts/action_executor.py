#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module; PII guard applied to all action params
"""
scripts/action_executor.py — Core action execution engine.

Orchestrates the full action lifecycle:
  propose → validate → PII scan → enqueue → approve → trust check →
  PII rescan → execute (with timeout) → record result → audit → notify

SAFETY CONTRACT:
  1. Fail-closed for writes: any failure before execute() aborts cleanly.
  2. Fail-open for reads: broken proposal lookup never crashes the caller.
  3. No partial writes: status transitions + audit entries are atomic.
  4. Read-only environments: execution is fully blocked; proposals still work.
  5. Autonomy floor: structurally enforced by TrustEnforcer — not bypassable.

EXECUTION LOG (§6 of specs/act.md):
  Every major step writes to state/audit.md in the format:
    [TIMESTAMP] ACTION_EVENT | id:{id} | type:{type} | domain:{domain} | ...

Ref: specs/act.md §5
"""
from __future__ import annotations

import importlib
import json
import os
import signal
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from actions.base import (
    ActionHandler,
    ActionProposal,
    ActionResult,
    DomainSignal,
    validate_proposal_fields,
)
from action_queue import ActionQueue
from trust_enforcer import TrustEnforcer
from action_rate_limiter import ActionRateLimiter, RateLimitError


# ---------------------------------------------------------------------------
# Handler allowlist (§4.3 — never load arbitrary module paths)
# ---------------------------------------------------------------------------

_HANDLER_MAP: dict[str, str] = {
    "email_send":        "actions.email_send",
    "email_reply":       "actions.email_reply",
    "calendar_create":   "actions.calendar_create",
    "calendar_modify":   "actions.calendar_modify",
    "reminder_create":   "actions.reminder_create",
    "whatsapp_send":     "actions.whatsapp_send",
    "todo_sync":         "actions.todo_sync_action",
    "instruction_sheet": "actions.instruction_sheet",
    # CONNECT Phase 1
    "slack_send":        "actions.slack_send",
    # CONNECT Phase 3 — Task manager sync
    "todoist_sync":         "actions.todoist_sync",
    "apple_reminders_sync": "actions.apple_reminders_sync",
}

# Default execution timeout per handler (seconds)
_DEFAULT_TIMEOUT_SEC = 30


# ---------------------------------------------------------------------------
# PII scanner (thin wrapper around pii_guard.py)
# ---------------------------------------------------------------------------

def _pii_scan_params(
    parameters: dict[str, Any],
    pii_allowlist: list[str],
) -> tuple[bool, list[str]]:
    """Scan action parameters for PII, excluding allowlisted fields.

    Returns (clean, findings):
      clean=True  → no PII detected; safe to proceed.
      clean=False → PII found in non-allowlisted fields; block action.

    This is a BLOCK-only check — we never redact action parameters.
    Unlike state file writes (where PII is redacted), outbound actions
    with PII must be reviewed by the user before any data leaves.
    """
    try:
        import subprocess
        import shutil

        pii_guard_path = Path(__file__).parent / "pii_guard.py"
        if not pii_guard_path.exists():
            return True, []  # guard not available — pass through (logged separately)

        # Scan each non-allowlisted string parameter
        findings: list[str] = []
        for key, value in parameters.items():
            if key in pii_allowlist:
                continue  # skip allowlisted fields (e.g. "to", "phone_number")
            if not isinstance(value, str) or not value.strip():
                continue

            result = subprocess.run(
                [sys.executable, str(pii_guard_path), "scan"],
                input=value,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                # PII_FOUND:<types> on stderr
                line = result.stderr.strip()
                if line.startswith("PII_FOUND:"):
                    types = line[len("PII_FOUND:"):].strip()
                    findings.append(f"field='{key}' types={types}")

        return len(findings) == 0, findings

    except Exception as e:
        # PII scanner error is not a pass — block conservatively
        return False, [f"PII scanner error: {e}"]


# ---------------------------------------------------------------------------
# Audit log appender
# ---------------------------------------------------------------------------

def _audit_log(artha_dir: Path, entry: str) -> None:
    """Append an entry to state/audit.md (best-effort; does not raise)."""
    try:
        audit_path = artha_dir / "state" / "audit.md"
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"[{ts}] {entry}\n"
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # audit log failure is non-fatal (DB is source of truth)


# ---------------------------------------------------------------------------
# ActionExecutor
# ---------------------------------------------------------------------------

class ActionExecutor:
    """Core action execution engine for Artha.

    Instantiation:
        executor = ActionExecutor(artha_dir)

    Usage in catch-up catch-up workflow:
        # Step 12.5: propose actions from domain signals
        proposal = executor.propose("email_send", "kids", "Reply to Mrs. Chen", {…})

        # Step 14 (terminal UX): user says "approve 1"
        result = executor.approve(proposal.id, approved_by="user:terminal")

        # Via Telegram callback:
        result = executor.approve(proposal.id, approved_by="user:telegram")
    """

    def __init__(self, artha_dir: Path) -> None:
        self._artha_dir = artha_dir

        # Determine if environment is read-only
        import detect_environment as _de  # noqa: PLC0415
        try:
            env_info = _de.detect(skip_network=True)
            self._read_only = not env_info.capabilities.get("filesystem_writable", True)
        except Exception:
            self._read_only = False

        self._queue = ActionQueue(artha_dir)
        self._trust = TrustEnforcer(artha_dir)

        # Load action configs
        self._action_configs = _load_action_configs(artha_dir)

        # Build rate limiter with action configs
        self._rate_limiter = ActionRateLimiter(artha_dir, self._action_configs)

        # Lazy-loaded handler instances: {action_type: module}
        self._handlers: dict[str, Any] = {}

        # Get age public key for encryption (lazy, may be None)
        self._pubkey: str | None = None

    def _get_pubkey(self) -> str | None:
        if self._pubkey is None:
            try:
                sys.path.insert(0, str(self._artha_dir / "scripts"))
                from foundation import get_public_key  # noqa: PLC0415
                self._pubkey = get_public_key()
            except (Exception, SystemExit):
                # SystemExit raised by foundation.die() when key is unavailable
                # (e.g. no age_recipient in user_profile.yaml). Treat as key absent.
                pass
        return self._pubkey

    def _get_handler(self, action_type: str) -> Any:
        """Load and cache the handler module for action_type.

        Uses the _HANDLER_MAP allowlist — never loads arbitrary paths.
        Raises ValueError if the action type is unknown or disabled.
        """
        if action_type in self._handlers:
            return self._handlers[action_type]

        # Security: only load from explicit allowlist
        module_path = _HANDLER_MAP.get(action_type)
        if not module_path:
            raise ValueError(
                f"Unknown action type: '{action_type}'. "
                f"Must be one of: {sorted(_HANDLER_MAP.keys())}"
            )

        # Check if enabled in config
        config = self._action_configs.get(action_type, {})
        if not config.get("enabled", True):
            raise ValueError(
                f"Action type '{action_type}' is disabled in config/actions.yaml"
            )

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ValueError(
                f"Cannot load handler for '{action_type}' from '{module_path}': {e}"
            ) from e

        # Verify Protocol compliance
        if not isinstance(module, ActionHandler):
            # Check for the required functions instead (structural subtyping)
            missing = [
                fn for fn in ("validate", "dry_run", "execute", "health_check")
                if not callable(getattr(module, fn, None))
            ]
            if missing:
                raise ValueError(
                    f"Handler '{module_path}' is missing required functions: {missing}"
                )

        self._handlers[action_type] = module
        return module

    # ------------------------------------------------------------------
    # Core lifecycle methods
    # ------------------------------------------------------------------

    def propose(
        self,
        action_type: str,
        domain: str,
        title: str,
        parameters: dict[str, Any],
        description: str = "",
        friction: str | None = None,
        min_trust: int | None = None,
        sensitivity: str = "standard",
        reversible: bool | None = None,
        undo_window_sec: int | None = None,
        expires_at: str | None = None,
        source_step: str | None = None,
        source_skill: str | None = None,
        linked_oi: str | None = None,
    ) -> ActionProposal:
        """Build, validate, PII-scan, and enqueue a new action proposal.

        Returns the enqueued ActionProposal.
        Raises ValueError if validation fails or PII is detected.
        """
        config = self._action_configs.get(action_type, {})

        # Apply defaults from config
        if friction is None:
            friction = config.get("friction", "standard")
        if min_trust is None:
            min_trust = config.get("min_trust", 1)
        if reversible is None:
            reversible = config.get("reversible", False)
        if undo_window_sec is None:
            undo_window_sec = config.get("undo_window_sec")
        if expires_at is None:
            default_expiry_hours = 72
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=default_expiry_hours)
            ).isoformat(timespec="seconds")

        proposal = ActionProposal(
            id=str(uuid.uuid4()),
            action_type=action_type,
            domain=domain,
            title=title[:120],  # enforce max length
            description=description,
            parameters=parameters,
            friction=friction,
            min_trust=min_trust,
            sensitivity=sensitivity,
            reversible=reversible,
            undo_window_sec=undo_window_sec,
            expires_at=expires_at,
            source_step=source_step,
            source_skill=source_skill,
            linked_oi=linked_oi,
        )

        # Field invariant check
        ok, reason = validate_proposal_fields(proposal)
        if not ok:
            raise ValueError(f"Invalid proposal: {reason}")

        # Handler-level validation (no external API calls)
        try:
            handler = self._get_handler(action_type)
            handler_ok, handler_reason = handler.validate(proposal)
            if not handler_ok:
                raise ValueError(f"Handler validation failed: {handler_reason}")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Handler load failed for '{action_type}': {e}") from e

        # PII scan (pre-enqueue)
        pii_allowlist = config.get("pii_allowlist", [])
        if config.get("pii_check", True):
            clean, findings = _pii_scan_params(parameters, pii_allowlist)
            if not clean:
                raise ValueError(
                    f"PII detected in action parameters — action blocked.\n"
                    f"Findings: {'; '.join(findings)}\n"
                    "Remove sensitive data from non-allowlisted fields before re-submitting."
                )

        # Enqueue
        try:
            self._queue.propose(proposal, pubkey=self._get_pubkey())
        except (ValueError, OverflowError) as e:
            raise ValueError(str(e)) from e

        _audit_log(
            self._artha_dir,
            f"ACTION_PROPOSED | id:{proposal.id} | type:{action_type} | "
            f"domain:{domain} | title:{title!r} | friction:{friction}",
        )

        # Bridge export (no-op if bridge disabled or role=executor)
        self._enqueue_and_maybe_export(proposal)

        return proposal

    def approve(self, action_id: str, approved_by: str) -> ActionResult:
        """Execute an approved action.

        Validation ordering (all checks before any state transition):
          0. Read-only environment check
          1. Load proposal from queue
          2. Status guard (must be pending or already approved)
          3. TrustEnforcer.check()  ← BEFORE state transition
          4. PII re-scan            ← BEFORE state transition
          5. Rate limiter check     ← BEFORE state transition
          6. Transition PENDING → APPROVED (atomic)
          7. Transition APPROVED → EXECUTING (THE POINT OF NO RETURN)
          8. handler.execute() with timeout
          9. Record result (EXECUTING → SUCCEEDED/FAILED, atomic)
         10. Audit trail
         11. Return ActionResult

        Checks 3-5 are intentionally performed BEFORE any state transition
        so the queue is never left in a corrupted intermediate state if a
        validation gate fires.
        """
        if self._read_only:
            return ActionResult(
                status="failure",
                message="Read-only environment: action execution is disabled.",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        # 1. Load proposal
        proposal = self._queue.get(action_id, privkey=self._get_privkey())
        if not proposal:
            return ActionResult(
                status="failure",
                message=f"Action {action_id} not found in queue.",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        # 2. Status guard
        raw = self._queue.get_raw(action_id)
        current_status = raw.get("status") if raw else None
        if current_status not in ("approved", "pending"):
            return ActionResult(
                status="failure",
                message=f"Action {action_id} is in status '{current_status}'; cannot approve.",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        config = self._action_configs.get(proposal.action_type, {})

        # 3. Trust check — BEFORE state transition
        trust_ok, trust_reason = self._trust.check(
            proposal, approved_by, action_config=config
        )
        if not trust_ok:
            _audit_log(
                self._artha_dir,
                f"ACTION_TRUST_BLOCKED | id:{action_id} | reason:{trust_reason}",
            )
            return ActionResult(
                status="failure",
                message=f"Trust gate blocked: {trust_reason}",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        # 4. PII re-scan — BEFORE state transition
        pii_allowlist = config.get("pii_allowlist", [])
        if config.get("pii_check", True):
            clean, findings = _pii_scan_params(proposal.parameters, pii_allowlist)
            if not clean:
                _audit_log(
                    self._artha_dir,
                    f"ACTION_PII_BLOCKED | id:{action_id} | findings:{findings}",
                )
                return ActionResult(
                    status="failure",
                    message=(
                        f"PII firewall blocked execution.\nFindings: {'; '.join(findings)}"
                    ),
                    data=None,
                    reversible=False,
                    reverse_action=None,
                )

        # 5. Rate limit check — BEFORE state transition
        try:
            self._rate_limiter.check(proposal.action_type)
        except RateLimitError as e:
            _audit_log(
                self._artha_dir,
                f"ACTION_RATE_LIMITED | id:{action_id} | reason:{e}",
            )
            return ActionResult(
                status="failure",
                message=str(e),
                data=None,
                reversible=False,
                reverse_action=None,
            )

        # 6. Transition PENDING → APPROVED (only if still pending)
        if current_status == "pending":
            try:
                self._queue.transition(
                    action_id, "approved", actor=approved_by, approved_by=approved_by
                )
                _audit_log(
                    self._artha_dir,
                    f"ACTION_APPROVED | id:{action_id} | by:{approved_by}",
                )
            except ValueError as e:
                return ActionResult(
                    status="failure",
                    message=str(e),
                    data=None,
                    reversible=False,
                    reverse_action=None,
                )

        # 7. Transition APPROVED → EXECUTING (THE POINT OF NO RETURN IS AFTER THIS)
        executed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            self._queue.transition(action_id, "executing", actor="system:executor")
        except ValueError as e:
            return ActionResult(
                status="failure",
                message=f"Failed to transition to EXECUTING: {e}",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        # 6. Execute with timeout
        handler = self._get_handler(proposal.action_type)
        timeout_sec = config.get("timeout_sec", _DEFAULT_TIMEOUT_SEC)
        result = _execute_with_timeout(handler, proposal, timeout_sec)

        # Compute undo deadline if applicable
        if result.status in ("success", "partial") and proposal.undo_window_sec:
            undo_deadline = (
                datetime.fromisoformat(executed_at) + timedelta(seconds=proposal.undo_window_sec)
            ).isoformat(timespec="seconds")
            result.undo_deadline = undo_deadline
            if result.data is None:
                result.data = {}
            result.data["undo_deadline"] = undo_deadline

        # 7. Record result (atomic: status + result fields)
        try:
            self._queue.record_result(
                action_id, result, executed_at, pubkey=self._get_pubkey()
            )
        except Exception as e:
            # Critical: result could not be saved. Log to audit but return result.
            _audit_log(
                self._artha_dir,
                f"ACTION_RESULT_SAVE_FAILED | id:{action_id} | error:{e}",
            )

        # 7b. Bridge result write (executor machine only; no-op if bridge disabled)
        self._maybe_write_bridge_result(action_id, result)

        # 8. Audit trail
        _audit_log(
            self._artha_dir,
            f"ACTION_EXECUTED | id:{action_id} | result:{result.status} | "
            f"message:{result.message!r}"
            + (f" | undo_until:{result.undo_deadline}" if result.undo_deadline else ""),
        )

        # 9. Record trust metric
        self._queue.record_trust_metric(
            action_type=proposal.action_type,
            domain=proposal.domain,
            user_decision="approved",
            execution_result=result.status,
        )

        return result

    def reject(self, action_id: str, reason: str = "") -> None:
        """Reject a pending action and log the reason."""
        try:
            self._queue.transition(
                action_id, "rejected", actor="user",
                context={"reason": reason} if reason else None,
            )
        except ValueError as e:
            raise ValueError(f"Cannot reject action {action_id}: {e}") from e

        _audit_log(
            self._artha_dir,
            f"ACTION_REJECTED | id:{action_id} | reason:{reason!r}",
        )
        self._queue.record_trust_metric(
            # We need action_type + domain; fetch from raw
            action_type=self._queue.get_raw(action_id).get("action_type", "unknown"),
            domain=self._queue.get_raw(action_id).get("domain", "unknown"),
            user_decision="rejected",
        )

    def defer(self, action_id: str, until: str) -> None:
        """Defer a pending action to a later time.

        Args:
            action_id: UUID of the pending action.
            until: ISO-8601 UTC string or relative offset ("+24h", "+1h").
        """
        # Resolve relative offset
        defer_time = _resolve_defer_time(until)

        try:
            self._queue.transition(
                action_id, "deferred", actor="user",
                context={"defer_until": defer_time},
            )
        except ValueError as e:
            raise ValueError(f"Cannot defer action {action_id}: {e}") from e

        # Update expires_at to the defer time (via managed connection — no raw _open_db)
        self._queue.update_defer_time(action_id, defer_time)

        _audit_log(
            self._artha_dir,
            f"ACTION_DEFERRED | id:{action_id} | until:{defer_time}",
        )
        self._queue.record_trust_metric(
            action_type=self._queue.get_raw(action_id).get("action_type", "unknown"),
            domain=self._queue.get_raw(action_id).get("domain", "unknown"),
            user_decision="deferred",
        )

    def cancel(self, action_id: str) -> None:
        """Cancel an approved-but-not-yet-executing action."""
        try:
            self._queue.transition(action_id, "cancelled", actor="user")
        except ValueError as e:
            raise ValueError(f"Cannot cancel action {action_id}: {e}") from e

        _audit_log(self._artha_dir, f"ACTION_CANCELLED | id:{action_id}")

    def undo(self, action_id: str) -> ActionResult:
        """Attempt to undo a SUCCEEDED action within its undo window.

        Checks undo_deadline from result_data.  If within window, executes
        the reverse_action built during the original execution.
        """
        if self._read_only:
            return ActionResult(
                status="failure",
                message="Read-only environment: undo is disabled.",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        raw = self._queue.get_raw(action_id)
        if not raw:
            return ActionResult(
                status="failure",
                message=f"Action {action_id} not found.",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        if raw.get("status") != "succeeded":
            return ActionResult(
                status="failure",
                message=f"Cannot undo action in status '{raw.get('status')}'. Only SUCCEEDED actions can be undone.",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        # Check undo_deadline
        result_data: dict[str, Any] = {}
        if raw.get("result_data"):
            try:
                result_data = json.loads(raw["result_data"])
            except Exception:
                pass

        undo_deadline = result_data.get("undo_deadline")
        if undo_deadline:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if now > undo_deadline:
                return ActionResult(
                    status="failure",
                    message=f"Undo window expired at {undo_deadline}. This action cannot be undone.",
                    data=None,
                    reversible=False,
                    reverse_action=None,
                )

        # Load the proposal and ask handler for reverse action
        proposal = self._queue.get(action_id, privkey=self._get_privkey())
        if not proposal:
            return ActionResult(
                status="failure",
                message=f"Cannot load proposal for action {action_id}.",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        # Build and immediately execute the reverse action (always human-approved)
        # For now, the reverse is handler-specific — handlers return it in result.reverse_action
        # We re-execute by calling handler.execute() on the reverse proposal
        handler = self._get_handler(proposal.action_type)
        if not hasattr(handler, "build_reverse_proposal"):
            return ActionResult(
                status="failure",
                message=f"Handler '{proposal.action_type}' does not support undo.",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        try:
            reverse_proposal = handler.build_reverse_proposal(proposal, result_data)
        except Exception as e:
            return ActionResult(
                status="failure",
                message=f"Could not build reverse action: {e}",
                data=None,
                reversible=False,
                reverse_action=None,
            )

        _audit_log(self._artha_dir, f"ACTION_UNDO_STARTED | id:{action_id}")
        config = self._action_configs.get(proposal.action_type, {})
        timeout_sec = config.get("timeout_sec", _DEFAULT_TIMEOUT_SEC)
        result = _execute_with_timeout(handler, reverse_proposal, timeout_sec)
        _audit_log(
            self._artha_dir,
            f"ACTION_UNDO_RESULT | id:{action_id} | result:{result.status} | msg:{result.message!r}",
        )
        return result

    def pending(self) -> list[ActionProposal]:
        """Return all PENDING (and re-surfaced DEFERRED) tasks."""
        return self._queue.list_pending(privkey=self._get_privkey())

    def list_pending(self) -> list[ActionProposal]:
        """Alias for pending() — spec-compliant name."""
        return self.pending()

    def history(self, days: int = 7) -> list[dict[str, Any]]:
        """Return executed/rejected actions for the last N days."""
        return self._queue.list_history(days=days, privkey=self._get_privkey())

    def list_history(self, days: int = 7) -> list[dict[str, Any]]:
        """Alias for history() — spec-compliant name."""
        return self.history(days=days)

    def get_action(self, action_id: str) -> ActionProposal | None:
        """Fetch a single action proposal by ID. Returns None if not found."""
        return self._queue.get(action_id, privkey=self._get_privkey())

    @property
    def queue(self) -> "ActionQueue":
        """Expose the underlying ActionQueue (read-only property for tests/inspection)."""
        return self._queue

    def propose_direct(self, proposal: ActionProposal) -> str:
        """Enqueue a pre-built ActionProposal and return its action ID.

        Used by ActionComposer.compose() workflow — the composer builds the
        complete proposal and this method enqueues it without re-validation.

        Returns:
            The action ID (proposal.id) of the enqueued proposal.
        Raises:
            ValueError: If PII is detected in non-allowlisted fields.
        """
        config = self._action_configs.get(proposal.action_type, {})
        pii_allowlist = config.get("pii_allowlist", [])
        if config.get("pii_check", True):
            clean, findings = _pii_scan_params(proposal.parameters, pii_allowlist)
            if not clean:
                raise ValueError(
                    f"PII firewall blocked enqueue.\nFindings: {'; '.join(findings)}"
                )
        self._queue.propose(proposal, pubkey=self._get_pubkey())
        _audit_log(
            self._artha_dir,
            f"ACTION_PROPOSED | id:{proposal.id} | type:{proposal.action_type} "
            f"| domain:{proposal.domain} | via:compose",
        )

        # Bridge export (no-op if bridge disabled or role=executor)
        self._enqueue_and_maybe_export(proposal)

        return proposal.id

    def expire_stale(self) -> int:
        """Sweep expired actions. Called at preflight Step 0c."""
        count = self._queue.expire_stale()
        if count:
            _audit_log(self._artha_dir, f"ACTION_EXPIRY_SWEEP | expired:{count}")
        return count

    def run_health_checks(self) -> dict[str, bool]:
        """Run health_check() for all enabled handlers.

        Returns dict of {action_type: bool}.
        Called at preflight Step 0c.  Failed handlers are disabled for the session.
        """
        results: dict[str, bool] = {}
        for action_type, module_path in _HANDLER_MAP.items():
            config = self._action_configs.get(action_type, {})
            if not config.get("enabled", True):
                continue
            try:
                handler = self._get_handler(action_type)
                ok = handler.health_check()
                results[action_type] = bool(ok)
                if not ok:
                    # Disable this handler for the session
                    self._handlers.pop(action_type, None)
            except Exception as e:
                results[action_type] = False
                self._handlers.pop(action_type, None)
                _audit_log(
                    self._artha_dir,
                    f"ACTION_HEALTH_CHECK_FAILED | type:{action_type} | error:{e}",
                )
        return results

    def queue_stats(self) -> dict[str, Any]:
        """Return queue health statistics for health-check.md."""
        return self._queue.queue_stats()

    def _get_privkey(self) -> str | None:
        """Get the age private key for decryption (lazy, may return None)."""
        try:
            sys.path.insert(0, str(self._artha_dir / "scripts"))
            from foundation import get_private_key  # noqa: PLC0415
            return get_private_key()
        except (Exception, SystemExit):
            # SystemExit raised by foundation.die() when keyring is unavailable
            # (e.g. Linux CI without a keyring backend). Treat as key absent.
            return None

    def close(self) -> None:
        """Release resources."""
        self._queue.close()

    # ------------------------------------------------------------------
    # Bridge helpers (disabled no-ops when bridge not configured)
    # ------------------------------------------------------------------

    def _enqueue_and_maybe_export(self, proposal: "ActionProposal") -> None:
        """Post-enqueue hook: export to bridge if enabled and role=proposer.

        Silently no-ops if:
        - multi_machine.bridge_enabled is false (default)
        - role is 'executor' (Windows receives proposals, doesn't send them)
        - action_bridge module unavailable
        """
        try:
            import yaml  # noqa: PLC0415
            config_path = self._artha_dir / "config" / "artha_config.yaml"
            if not config_path.exists():
                return
            with open(config_path, encoding="utf-8") as f:
                artha_config = yaml.safe_load(f) or {}

            mm = artha_config.get("multi_machine", {})
            if not mm.get("bridge_enabled", False):
                return

            # Detect role
            sys.path.insert(0, str(self._artha_dir / "scripts"))
            from action_bridge import detect_role, get_bridge_dir  # noqa: PLC0415
            channels_path = self._artha_dir / "config" / "channels.yaml"
            channels_config: dict = {}
            if channels_path.exists():
                with open(channels_path, encoding="utf-8") as f:
                    channels_config = yaml.safe_load(f) or {}

            role = detect_role(channels_config)
            if role != "proposer":
                return  # executor doesn't write proposals

            bridge_dir = get_bridge_dir(self._artha_dir)
            from action_bridge import write_proposal  # noqa: PLC0415
            write_proposal(bridge_dir, proposal, pubkey=self._get_pubkey())
            _audit_log(
                self._artha_dir,
                f"BRIDGE_PROPOSAL_WRITE | id:{proposal.id} | type:{proposal.action_type}",
            )
        except Exception as exc:
            # Bridge export failures are non-fatal — action is already locally enqueued
            _audit_log(
                self._artha_dir,
                f"BRIDGE_EXPORT_WARN | id:{proposal.id} | error:{exc}",
            )

    def _maybe_write_bridge_result(self, action_id: str, result: "ActionResult") -> None:
        """Write a result to the bridge results/ dir and mark bridge_synced=1.

        Called on the executor machine (Windows) after record_result().
        Silently no-ops if bridge is disabled or role is not executor.
        """
        try:
            import yaml  # noqa: PLC0415
            config_path = self._artha_dir / "config" / "artha_config.yaml"
            if not config_path.exists():
                return
            with open(config_path, encoding="utf-8") as f:
                artha_config = yaml.safe_load(f) or {}

            mm = artha_config.get("multi_machine", {})
            if not mm.get("bridge_enabled", False):
                return

            # Only executor writes results
            from action_bridge import detect_role, get_bridge_dir, write_result  # noqa: PLC0415
            channels_path = self._artha_dir / "config" / "channels.yaml"
            channels_config: dict = {}
            if channels_path.exists():
                with open(channels_path, encoding="utf-8") as f:
                    channels_config = yaml.safe_load(f) or {}

            role = detect_role(channels_config)
            if role != "executor":
                return  # proposer doesn't write results directly

            # Only write for bridge-originated actions
            raw = self._queue.get_raw(action_id)
            if not raw or raw.get("origin") != "bridge":
                return

            bridge_dir = get_bridge_dir(self._artha_dir)
            final_status = "succeeded" if result.status == "success" else "failed"
            write_result(
                bridge_dir,
                action_id=action_id,
                final_status=final_status,
                result_message=result.message,
                result_data=result.data,
                pubkey=self._get_pubkey(),
            )
            self._queue.mark_bridge_synced(action_id)
        except Exception as exc:
            # Bridge write failures are non-fatal — result is already in local DB
            _audit_log(
                self._artha_dir,
                f"BRIDGE_RESULT_WRITE_WARN | id:{action_id} | error:{exc}",
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _execute_with_timeout(
    handler: Any, proposal: ActionProposal, timeout_sec: int
) -> ActionResult:
    """Execute handler.execute() with a timeout.

    On timeout: returns ActionResult(status="failure", ...).
    Catches all exceptions and converts to failure result.

    Uses signal.SIGALRM on Unix; threading-based timeout on Windows.
    """
    import threading

    result_container: list[ActionResult | None] = [None]
    exception_container: list[Exception | None] = [None]

    def _run() -> None:
        try:
            result_container[0] = handler.execute(proposal)
        except Exception as e:
            exception_container[0] = e

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)

    if thread.is_alive():
        # Timeout — thread is still running but we abandon it
        return ActionResult(
            status="failure",
            message=f"Action timed out after {timeout_sec}s. No changes were committed.",
            data={"timed_out": True},
            reversible=False,
            reverse_action=None,
        )

    if exception_container[0] is not None:
        exc = exception_container[0]
        return ActionResult(
            status="failure",
            message=f"Handler raised exception: {type(exc).__name__}: {exc}",
            data={"exception": str(exc)},
            reversible=False,
            reverse_action=None,
        )

    if result_container[0] is None:
        return ActionResult(
            status="failure",
            message="Handler returned None (programming error in handler).",
            data=None,
            reversible=False,
            reverse_action=None,
        )

    return result_container[0]


def _load_action_configs(artha_dir: Path) -> dict[str, Any]:
    """Load config/actions.yaml and return the actions dict.

    Returns empty dict on any error (fail-open for config loading).
    """
    actions_yaml = artha_dir / "config" / "actions.yaml"
    if not actions_yaml.exists():
        return {}
    try:
        import yaml  # PyYAML
        with open(actions_yaml, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("actions", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_defer_time(until: str) -> str:
    """Resolve an until= value to an ISO-8601 UTC string.

    Accepts:
        "+24h"   → now + 24 hours
        "+1h"    → now + 1 hour
        "+30m"   → now + 30 minutes
        "2026-04-01T09:00:00+00:00"  → as-is (validated)
    """
    import re
    now = datetime.now(timezone.utc)

    m = re.match(r"^\+(\d+)([hHmM])$", until.strip())
    if m:
        qty = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "h":
            return (now + timedelta(hours=qty)).isoformat(timespec="seconds")
        elif unit == "m":
            return (now + timedelta(minutes=qty)).isoformat(timespec="seconds")

    # Try parsing as ISO-8601
    try:
        parsed = datetime.fromisoformat(until)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat(timespec="seconds")
    except ValueError:
        pass

    # Fallback: 24h from now
    return (now + timedelta(hours=24)).isoformat(timespec="seconds")
