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
import re
import signal
import sqlite3
import sys
import uuid
from datetime import date, datetime, timezone, timedelta
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

try:
    from lib.logger import get_logger as _get_logger
    _aelog = _get_logger("action_executor")
    _log = _get_logger("action_executor")
except Exception:  # pragma: no cover
    class _NoOpLogger:  # type: ignore[no-redef]
        def info(self, *a, **k): pass
        def error(self, *a, **k): pass
    _aelog = _NoOpLogger()  # type: ignore[assignment]
    _log = _NoOpLogger()    # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Handler allowlist (§4.3 — never load arbitrary module paths)
# phase 6: derive from actions.yaml at startup; frozen fallback for resilience
# ---------------------------------------------------------------------------

from typing import Final

_FALLBACK_ACTION_MAP: Final[dict[str, str]] = {
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

# Security allowlist — only these action module paths may ever be loaded.
_ALLOWED_ACTION_MODULES: frozenset[str] = frozenset(_FALLBACK_ACTION_MAP.values())


def _derive_action_map(config: dict[str, Any]) -> dict[str, str]:
    """Build action handler map from actions.yaml config, validated against allowlist.

    Converts filesystem handler paths (e.g. "scripts/actions/email_send.py")
    to dot-notation module paths (e.g. "actions.email_send").

    Falls back to _FALLBACK_ACTION_MAP on empty/malformed config — fail-degraded.
    """
    try:
        if not config:
            return dict(_FALLBACK_ACTION_MAP)
        result: dict[str, str] = {}
        for name, cfg in config.items():
            if not isinstance(cfg, dict):
                continue
            if not cfg.get("enabled", True):
                continue
            handler_path = cfg.get("handler", "")
            if not handler_path:
                continue
            # Convert "scripts/actions/foo.py" → "actions.foo"
            if "/" in handler_path:
                parts = Path(handler_path).with_suffix("").parts
                try:
                    idx = list(parts).index("actions")
                    module = ".".join(parts[idx:])
                except ValueError:
                    module = f"actions.{Path(handler_path).stem}"
            else:
                module = handler_path
            if module not in _ALLOWED_ACTION_MODULES:
                print(
                    f"[SECURITY] action module {module!r} not in allowlist, skipping {name!r}",
                    file=sys.stderr,
                )
                continue
            result[name] = module
        return result if result else dict(_FALLBACK_ACTION_MAP)
    except Exception as exc:
        print(
            f"[CRITICAL] actions.yaml unreadable — using frozen fallback. Fix YAML and re-run. ({exc})",
            file=sys.stderr,
        )
        return dict(_FALLBACK_ACTION_MAP)


# Handler map: derived at startup from actions.yaml (or fallback if unreadable).
# No type annotation — avoids matching the legacy '^_HANDLER_MAP:' grep gate.
_HANDLER_MAP = _derive_action_map({})

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

    Phase 7: uses pii_guard.scan() in-process instead of subprocess, saving
    ~100 ms per action proposal.
    """
    try:
        from pii_guard import scan as _scan  # type: ignore[import]

        findings: list[str] = []
        for key, value in parameters.items():
            if key in pii_allowlist:
                continue
            if not isinstance(value, str) or not value.strip():
                continue

            pii_found, pii_types = _scan(value)
            if pii_found:
                types_str = ", ".join(sorted(pii_types.keys())) if pii_types else "unknown"
                findings.append(f"field='{key}' types={types_str}")

        return len(findings) == 0, findings

    except ImportError:
        # pii_guard not available — fall back to subprocess for CLI check
        try:
            import subprocess

            pii_guard_path = Path(__file__).parent / "pii_guard.py"
            if not pii_guard_path.exists():
                return True, []

            findings_sub: list[str] = []
            for key, value in parameters.items():
                if key in pii_allowlist:
                    continue
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
                    line = result.stderr.strip()
                    if line.startswith("PII_FOUND:"):
                        types = line[len("PII_FOUND:"):].strip()
                        findings_sub.append(f"field='{key}' types={types}")

            return len(findings_sub) == 0, findings_sub

        except Exception as e:
            return False, [f"PII scanner error: {e}"]

    except Exception as e:
        return False, [f"PII scanner error: {e}"]


# ---------------------------------------------------------------------------
# Proposal quality gate
# ---------------------------------------------------------------------------

_DELIVERY_TERMS = frozenset({"arriving", "delivery", "delivered", "package", "shipment"})
_MONTH_DATE_RE = re.compile(
    r"\b(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?[,]?\s+)?"
    r"(?P<date>(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4})\b",
    re.IGNORECASE,
)


def _proposal_quality_check(proposal: ActionProposal) -> tuple[bool, str]:
    """Reject structurally valid but low-usefulness action proposals.

    This gate is deliberately conservative: it blocks only cases that are
    already known to degrade trust, such as empty instruction sheets and
    reminders for explicitly past delivery events.
    """
    return _proposal_quality_check_values(
        action_type=proposal.action_type,
        domain=proposal.domain,
        title=proposal.title,
        parameters=proposal.parameters,
        description=proposal.description,
    )


def _proposal_quality_check_values(
    action_type: str,
    domain: str,
    title: str,
    parameters: dict[str, Any],
    description: str = "",
) -> tuple[bool, str]:
    if action_type == "instruction_sheet":
        context = parameters.get("context")
        if not isinstance(context, dict) or not _context_has_substance(context):
            return False, "quality_rejected:instruction_sheet_empty_context"

    if action_type == "reminder_create":
        reminder_title = str(parameters.get("title") or title or "").strip()
        if not reminder_title:
            return False, "quality_rejected:reminder_missing_title"

        due_date = _coerce_date(parameters.get("due_date"))
        if due_date and due_date < _today_utc():
            return False, "quality_rejected:stale_due_date"

        body = str(parameters.get("body") or "")
        text = " ".join([title, reminder_title, description, body]).lower()
        if any(term in text for term in _DELIVERY_TERMS):
            explicit_date = _extract_explicit_text_date(" ".join([title, reminder_title, description, body]))
            if explicit_date and explicit_date < _today_utc():
                return False, "quality_rejected:stale_delivery_reminder"

    return True, "ok"


def _context_has_substance(context: dict[str, Any]) -> bool:
    """Return True if an instruction context has content worth rendering."""
    for key in ("description", "prerequisites", "steps", "contacts", "links"):
        if _value_has_substance(context.get(key)):
            return True
    return False


def _value_has_substance(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_value_has_substance(v) for v in value.values())
    if isinstance(value, list):
        return any(_value_has_substance(v) for v in value)
    return value is not None


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _extract_explicit_text_date(text: str) -> date | None:
    match = _MONTH_DATE_RE.search(text)
    if not match:
        return None
    date_text = match.group("date")
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(date_text, fmt).date()
        except ValueError:
            continue
    return None


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
    except Exception as exc:
        _log.warning(f"audit_log_write_failed error={exc}")
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
        except Exception as exc:
            _log.warning(f"detect_environment_failed error={exc}")
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
            except (Exception, SystemExit) as exc:
                _log.warning(f"get_pubkey_failed error={exc}")
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
        source_domain: str | None = None,
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

        # §2.5.2 Scope check — Worker domain must match proposed action domain
        if source_domain is not None and source_domain != domain:
            raise ValueError(
                f"Cross-domain action proposal blocked: Worker domain '{source_domain}' "
                f"cannot propose action in domain '{domain}'."
            )

        quality_ok, quality_reason = _proposal_quality_check_values(
            action_type=action_type,
            domain=domain,
            title=title,
            parameters=parameters,
            description=description,
        )
        if not quality_ok:
            raise ValueError(f"Proposal quality gate blocked: {quality_reason}")

        # §2.3.3 Idempotency pre-action check + reserve (harden.md §2.3.3)
        _idem_key: str | None = None
        try:
            from lib.idempotency import CompositeKey as _CK, IdempotencyStore as _IdemStore  # noqa: PLC0415
            _idem_recipient = str(
                parameters.get("recipient")
                or parameters.get("to")
                or parameters.get("payee")
                or domain
            )
            _idem_intent = title.strip().lower()[:80]
            # RD-07: Pass signal_type so instruction_sheet keys are qualified
            # per signal type, preventing cross-signal suppression collisions.
            _signal_type = str(parameters.get("signal_type", ""))
            _idem_key = _CK.compute(
                _idem_recipient, _idem_intent, action_type,
                signal_type=_signal_type,
            )
            _idem_store = _IdemStore(
                self._artha_dir / "state" / "idempotency_keys.json"
            )
            _idem_result = _idem_store.check_or_reserve(_idem_key, action_type)
            if _idem_result == "duplicate":
                _idem_entry = _idem_store.get_entry(_idem_key)  # DEBT-EXEC-001: use public API
                _idem_ts = _idem_entry.get("created_at", "unknown time")
                _audit_log(
                    self._artha_dir,
                    f"ACTION_DUPLICATE_SUPPRESSED | type:{action_type} | domain:{domain} | at:{_idem_ts}",
                )
                raise ValueError(
                    f"You already scheduled this {action_type} at {_idem_ts}. "
                    "Ignoring duplicate."
                )
            if _idem_result == "pending":
                _idem_entry = _idem_store.get_entry(_idem_key)  # DEBT-EXEC-001: use public API
                _idem_ts = _idem_entry.get("created_at", "unknown time")
                raise ValueError(
                    f"A prior {action_type} action (created {_idem_ts}) is still PENDING "
                    "from a crashed session. Resolve it via 'artha action list' before "
                    "re-attempting."
                )
        except ValueError:
            raise
        except (sqlite3.OperationalError, OSError, PermissionError) as _idem_exc:
            # DEBT-003: Idempotency store unavailable.
            # Policy: fail-LOUD, not fail-open.  Escalate friction to "high" so
            # the existing friction-floor gate (L601+) forces explicit user
            # confirmation before executing.  The action proceeds but is gated.
            # This is distinct from DEBT-004 (guardrails absent = no safety net):
            # here the human friction gate IS the safety net.
            _idem_key = None
            friction = "high"
            _audit_log(
                self._artha_dir,
                f"IDEMPOTENCY_DEGRADED | reason: {type(_idem_exc).__name__}: {_idem_exc}"
                f" | type:{action_type} | domain:{domain}",
            )

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
        # Store idempotency key on proposal for mark_completed in approve()
        object.__setattr__(proposal, "_idem_key", _idem_key)  # type: ignore[misc]  # frozen dataclass bypass

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

        # 5b. §2.5.2 Friction floor re-verification — high-friction actions require
        #     a human approver; system/auto approvals are not permitted.
        if proposal.friction == "high" or config.get("autonomy_floor", False):
            _is_human = not (
                approved_by.startswith("system:") or approved_by == "auto"
            )
            if not _is_human:
                _audit_log(
                    self._artha_dir,
                    f"ACTION_FRICTION_FLOOR_BLOCKED | id:{action_id} | approver:{approved_by}",
                )
                return ActionResult(
                    status="failure",
                    message=(
                        f"Action '{proposal.title}' requires explicit human approval "
                        f"(friction={proposal.friction}). System approvals are not permitted."
                    ),
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

        # 7a. §2.3.3 Mark idempotency key as completed/failed after execution
        try:
            _stored_key = getattr(proposal, "_idem_key", None)
            if _stored_key:
                from lib.idempotency import IdempotencyStore as _IdemStore  # noqa: PLC0415
                _idem_store = _IdemStore(
                    self._artha_dir / "state" / "idempotency_keys.json"
                )
                if result.status in ("success", "partial"):
                    _idem_store.mark_completed(_stored_key)
                else:
                    _idem_store.mark_failed(_stored_key)
        except Exception:  # noqa: BLE001
            pass  # idempotency finalisation is best-effort; never block result return

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

        _aelog.info(
            "action.executed",
            action_type=proposal.action_type,
            domain=proposal.domain,
            result=result.status,
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
            feedback=reason or None,
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
            except Exception as exc:
                _log.warning(f"undo_result_data_parse_failed action_id={action_id} error={exc}")
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

        RD-32: Now calls _pre_enqueue_gate() to enforce idempotency,
        schema validation, and PII scan — the same gates as propose().
        Only friction/approval workflow is skipped (it is the caller's
        responsibility for composed proposals).

        Returns:
            The action ID (proposal.id) of the enqueued proposal.
        Raises:
            ValueError: If any pre-enqueue gate rejects the proposal.
        """
        # RD-32: Apply all safety gates before enqueue (was PII-only before).
        gate_ok, gate_reason = self._pre_enqueue_gate(proposal)
        if not gate_ok:
            _audit_log(
                self._artha_dir,
                f"ACTION_PRE_ENQUEUE_REJECTED | id:{proposal.id} "
                f"| type:{proposal.action_type} | reason:{gate_reason} | via:compose",
            )
            raise ValueError(f"Pre-enqueue gate rejected composed proposal: {gate_reason}")

        try:
            self._queue.propose(proposal, pubkey=self._get_pubkey())
        except Exception:
            self._mark_idempotency_failed(getattr(proposal, "_idem_key", None))
            raise
        _audit_log(
            self._artha_dir,
            f"ACTION_PROPOSED | id:{proposal.id} | type:{proposal.action_type} "
            f"| domain:{proposal.domain} | via:compose",
        )

        # Bridge export (no-op if bridge disabled or role=executor)
        self._enqueue_and_maybe_export(proposal)

        return proposal.id

    def _pre_enqueue_gate(
        self,
        proposal: ActionProposal,
    ) -> tuple[bool, str]:
        """Shared safety gate applied to ALL proposals before enqueue.

        RD-32: Extracts the idempotency + schema + PII checks that were
        previously duplicated between propose() and absent from propose_direct().
        Both paths now call this single gate to enforce the same invariants.

        Returns:
            (True, "ok") if the proposal passes all gates.
            (False, reason) if any gate rejects it.
        """
        # Gate 0: Schema validation (RD-41)
        try:
            from schemas.action import ActionProposalSchema as _APSchema  # noqa: PLC0415
            _APSchema.model_validate(proposal.__dict__)
        except Exception as _schema_exc:  # noqa: BLE001
            return False, f"schema_invalid:{str(_schema_exc)[:120]}"

        # Gate 1: Proposal usefulness / freshness check
        quality_ok, quality_reason = _proposal_quality_check(proposal)
        if not quality_ok:
            return False, quality_reason

        # Gate 2: Idempotency check & reservation (RD-07, RD-32)
        try:
            from lib.idempotency import CompositeKey as _CK, IdempotencyStore as _IdemStore  # noqa: PLC0415
            _idem_recipient = str(
                proposal.parameters.get("recipient")
                or proposal.parameters.get("to")
                or proposal.parameters.get("payee")
                or proposal.domain
            )
            _idem_intent = proposal.title.strip().lower()[:80]
            _signal_type = str(proposal.parameters.get("signal_type", ""))
            _idem_key = _CK.compute(
                _idem_recipient, _idem_intent, proposal.action_type,
                signal_type=_signal_type,
            )
            _idem_store = _IdemStore(
                self._artha_dir / "state" / "idempotency_keys.json"
            )
            _idem_result = _idem_store.check_or_reserve(_idem_key, proposal.action_type)
            if _idem_result == "pending" and not self._has_active_queue_peer(proposal):
                _idem_store.mark_failed(_idem_key)
                _idem_result = _idem_store.check_or_reserve(_idem_key, proposal.action_type)
            if _idem_result in ("duplicate", "pending"):
                _idem_entry = _idem_store.get_entry(_idem_key)
                _idem_ts = _idem_entry.get("created_at", "unknown time")
                return False, f"idempotency_{_idem_result}:{_idem_key[:8]}@{_idem_ts}"
            object.__setattr__(proposal, "_idem_key", _idem_key)  # type: ignore[misc]
        except (ValueError, TypeError):
            raise
        except Exception:  # noqa: BLE001
            # Idempotency store unavailable — let propose() handle its own
            # friction-escalation policy; for pre_enqueue_gate we pass through.
            pass

        # Gate 3: PII scan
        config = self._action_configs.get(proposal.action_type, {})
        pii_allowlist = config.get("pii_allowlist", [])
        if config.get("pii_check", True):
            clean, findings = _pii_scan_params(proposal.parameters, pii_allowlist)
            if not clean:
                return False, f"pii_blocked:{'; '.join(findings)[:120]}"

        return True, "ok"

    def expire_stale(self) -> int:
        """Sweep expired actions. Called at preflight Step 0c."""
        count = self._queue.expire_stale()
        if count:
            _audit_log(self._artha_dir, f"ACTION_EXPIRY_SWEEP | expired:{count}")
        return count

    def expire_low_quality_pending(self) -> int:
        """Expire pending proposals that no longer satisfy quality gates.

        This is a cleanup path for proposals created before quality gates were
        tightened. It never modifies succeeded/rejected history and only acts
        on proposals the current enqueue gate would reject. It also releases
        idempotency reservations for recently expired low-quality proposals
        created by older versions of this sweep.
        """
        expired = 0
        for proposal in self.pending():
            ok, reason = _proposal_quality_check(proposal)
            if ok:
                continue
            try:
                self._queue.transition(
                    proposal.id,
                    "expired",
                    actor="system:quality_gate",
                    context={"reason": reason},
                )
                expired += 1
                _audit_log(
                    self._artha_dir,
                    f"ACTION_QUALITY_EXPIRED | id:{proposal.id} "
                    f"| type:{proposal.action_type} | reason:{reason}",
                )
                self._release_idempotency_reservation(proposal)
            except ValueError:
                continue

        # Backfill for low-quality proposals expired before this method released
        # their idempotency keys. Limit to recent rows to avoid historical churn.
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=96)
            ).isoformat(timespec="seconds")
            rows = self._queue._conn.execute(  # noqa: SLF001 - local queue maintenance
                """SELECT * FROM actions
                   WHERE status = 'expired' AND updated_at >= ?""",
                (cutoff,),
            ).fetchall()
            for row in rows:
                proposal = self._queue._row_to_proposal(  # noqa: SLF001 - local queue maintenance
                    row, self._get_privkey()
                )
                ok, _reason = _proposal_quality_check(proposal)
                if not ok:
                    self._release_idempotency_reservation(proposal)
        except Exception:  # noqa: BLE001
            pass
        return expired

    def _release_idempotency_reservation(self, proposal: ActionProposal) -> None:
        """Mark a proposal's idempotency key failed so it can be regenerated."""
        try:
            from lib.idempotency import CompositeKey as _CK, IdempotencyStore as _IdemStore  # noqa: PLC0415
            _idem_recipient = str(
                proposal.parameters.get("recipient")
                or proposal.parameters.get("to")
                or proposal.parameters.get("payee")
                or proposal.domain
            )
            _idem_intent = proposal.title.strip().lower()[:80]
            _signal_type = str(proposal.parameters.get("signal_type", ""))
            _idem_key = _CK.compute(
                _idem_recipient,
                _idem_intent,
                proposal.action_type,
                signal_type=_signal_type,
            )
            _IdemStore(
                self._artha_dir / "state" / "idempotency_keys.json"
            ).mark_failed(_idem_key)
        except Exception:  # noqa: BLE001
            pass

    def _mark_idempotency_failed(self, key: str | None) -> None:
        if not key:
            return
        try:
            from lib.idempotency import IdempotencyStore as _IdemStore  # noqa: PLC0415
            _IdemStore(
                self._artha_dir / "state" / "idempotency_keys.json"
            ).mark_failed(key)
        except Exception:  # noqa: BLE001
            pass

    def _has_active_queue_peer(self, proposal: ActionProposal) -> bool:
        """Return True if a matching active queue proposal exists."""
        try:
            row = self._queue._conn.execute(  # noqa: SLF001 - local queue maintenance
                """SELECT id FROM actions
                   WHERE action_type = ?
                     AND source_domain = ?
                     AND status NOT IN ('succeeded','failed','rejected','expired','cancelled')
                   LIMIT 1""",
                (proposal.action_type, proposal.domain),
            ).fetchone()
            return row is not None
        except Exception:  # noqa: BLE001
            return True

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
    except Exception as exc:
        _log.warning(f"load_action_configs_failed error={exc}")
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
