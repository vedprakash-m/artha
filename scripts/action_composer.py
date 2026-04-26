#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data in this file
"""
scripts/action_composer.py — Signal-to-ActionProposal mapping layer.

The ActionComposer is the bridge between domain intelligence (what Artha knows)
and action proposals (what Artha can do).  It converts DomainSignal objects
produced by deterministic skills into ActionProposal objects ready for the
human-approval queue.

DESIGN CONTRACT (§10 invariants):
  - ONE signal → AT MOST ONE action proposal.
  - The Composer NEVER decides whether to execute — only structures the proposal.
  - Signals come from deterministic skills (Steps 8–11), never from LLM
    inference on raw email text.  This eliminates prompt injection risk.
  - Friction is the MAXIMUM of (signal domain sensitivity, action base friction).
  - Immigration / finance domains always escalate to friction: high.
  - sensitivity: high/critical always implies friction: standard minimum.

Ref: specs/act.md §10
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import ActionProposal, DomainSignal

# ---------------------------------------------------------------------------
# Signal-to-action routing table (§10.2)
# ---------------------------------------------------------------------------
# Each entry: signal_type → (action_type, base_friction, min_trust, reversible, undo_window_sec)
# Friction values: "low" | "standard" | "high"
# min_trust: 0=observe(never auto), 1=propose, 2=pre-approve
#
# SOLE AUTHORITY: config/signal_routing.yaml
# _FALLBACK_SIGNAL_ROUTING is populated at import time directly from the YAML
# file so that _load_signal_routing() can provide a bootstrap copy when
# load_config() returns an empty dict (e.g. during transient I/O failures or
# first-run before config_loader cache is warm).
# Invariant enforced by tests: set(_FALLBACK_SIGNAL_ROUTING) == set(signal_routing.yaml).
try:
    import yaml as _yaml_mod
    _routing_path = Path(__file__).resolve().parent.parent / "config" / "signal_routing.yaml"
    with open(_routing_path, encoding="utf-8") as _f:
        _FALLBACK_SIGNAL_ROUTING: dict[str, dict] = _yaml_mod.safe_load(_f) or {}
    del _yaml_mod, _routing_path
except Exception:
    _FALLBACK_SIGNAL_ROUTING = {}

_REQUIRED_ROUTE_FIELDS = frozenset({"action_type", "friction", "min_trust", "reversible", "undo_window_sec"})

# Domains that always escalate friction to "high" (§10.3 rule 4)
_HIGH_FRICTION_DOMAINS = frozenset({"immigration", "finance", "insurance", "estate"})

# Friction ordering for max() computation
_FRICTION_ORDER = {"low": 0, "standard": 1, "high": 2}
_FRICTION_NAMES = ["low", "standard", "high"]

# Default action proposal expiry (hours to auto-expire if not acted on)
_DEFAULT_EXPIRY_HOURS = 72

# Known-valid action types for routing table validation (Phase 6 §10.2.3)
# INVARIANT: must remain in sync with _FALLBACK_ACTION_MAP in action_executor.py.
# See specs/actions-reloaded.md §5.2.3 — "decision_log_proposal" removed until
# actions/decision_log_proposal.py handler is implemented in V1.1.
_ALLOWED_ACTION_TYPES: frozenset[str] = frozenset({
    "email_send", "email_reply", "calendar_create", "calendar_modify",
    "reminder_create", "whatsapp_send", "todo_sync", "instruction_sheet",
    "slack_send", "todoist_sync", "apple_reminders_sync",
    # "decision_log_proposal" — V1.1: add when handler is implemented
})


# Cached routing table — loaded once, reused across compose() calls
_CACHED_ROUTING: dict[str, dict] | None = None


def _validate_routing_table(routing: dict[str, dict] | None = None) -> None:
    """Validate a routing table for completeness and known action types.

    Operates on ``_FALLBACK_SIGNAL_ROUTING`` when called with no argument.
    Emits [WARNING] to stderr on any validation issue but never raises.

    Called by:
    - _load_signal_routing() to validate the just-loaded YAML table.
    - Tests (test_t6_5, test_t6_6) to assert table correctness.
    """
    if routing is None:
        routing = _FALLBACK_SIGNAL_ROUTING
    errors: list[str] = []
    for signal_type, route in routing.items():
        if not isinstance(route, dict):
            errors.append(f"  {signal_type}: expected dict, got {type(route).__name__}")
            continue
        missing = _REQUIRED_ROUTE_FIELDS - set(route.keys())
        if missing:
            errors.append(f"  {signal_type}: missing fields {sorted(missing)}")
        atype = route.get("action_type")
        if atype and atype not in _ALLOWED_ACTION_TYPES:
            errors.append(f"  {signal_type}: unknown action_type '{atype}'")
    if errors:
        print(
            "[WARNING] signal_routing.yaml validation errors:\n"
            + "\n".join(errors),
            file=sys.stderr,
        )


def _load_signal_routing() -> dict[str, dict]:
    """Load signal routing from config/signal_routing.yaml (sole authority).

    Validates every entry has all required fields and all action_types are
    known. Emits loud warnings on any issues but does not raise to avoid
    breaking imports during development.

    Ref: specs/action.md Phase 4 — routing table consolidation
    """
    global _CACHED_ROUTING  # noqa: PLW0603
    if _CACHED_ROUTING is not None:
        return _CACHED_ROUTING

    routing: dict[str, dict] = {}
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        yaml_routing = load_config("signal_routing")
        if isinstance(yaml_routing, dict) and yaml_routing:
            # RD-48: Merge YAML over fallback instead of replacing, so any
            # signal types present in the fallback but accidentally omitted
            # from the YAML file still get a route rather than silently
            # disappearing.  YAML entries win on key collision (intended).
            base = dict(_FALLBACK_SIGNAL_ROUTING)
            base.update(yaml_routing)
            routing = base
    except Exception as exc:
        print(
            f"[CRITICAL] signal_routing.yaml failed to load: {exc}\n"
            "  Action routing is EMPTY — no signals will map to actions.",
            file=sys.stderr,
        )

    _validate_routing_table(routing)

    if not routing:
        print(
            "[WARNING] Signal routing table is empty — falling back to built-in routing.",
            file=sys.stderr,
        )
        _CACHED_ROUTING = _FALLBACK_SIGNAL_ROUTING
        return _FALLBACK_SIGNAL_ROUTING

    _CACHED_ROUTING = routing
    return routing


# Validate at import time — fail fast on bad config
_load_signal_routing()


# ---------------------------------------------------------------------------
# ActionComposer class
# ---------------------------------------------------------------------------

class ActionComposer:
    """Maps domain signals to ActionProposals.

    Instantiation:
        composer = ActionComposer(actions_config)

    The `actions_config` dict is the parsed content of config/actions.yaml,
    used to look up base friction and enabled status per action_type.

    Usage in Step 12.5:
        for signal in domain_signals:
            proposal = composer.compose(signal)
            if proposal:
                executor.propose_direct(proposal)
    """

    def __init__(
        self,
        actions_config: dict[str, Any] | None = None,
        artha_dir: "Path | None" = None,
    ) -> None:
        """Initialize with actions.yaml config for friction lookups.

        Args:
            actions_config: Pre-loaded YAML dict (full file, with "actions" key).
                            If omitted, pass artha_dir to load automatically.
            artha_dir:      Path to Artha workspace root. Used to load
                            config/actions.yaml when actions_config is not provided.
        """
        self._config: dict[str, Any] = {}
        raw: dict[str, Any] | None = actions_config

        # Auto-load from artha_dir if no explicit config provided
        if raw is None and artha_dir is not None:
            try:
                from lib.config_loader import load_config  # noqa: PLC0415
                raw = load_config("actions", str(artha_dir / "config")) or None
            except Exception:
                raw = None

        if raw and isinstance(raw, dict):
            actions_section = raw.get("actions", {})
            if isinstance(actions_section, dict):
                # Standard dict-keyed format: {action_type: config_dict}
                for atype, cfg in actions_section.items():
                    if isinstance(cfg, dict):
                        self._config[atype] = cfg
            elif isinstance(actions_section, list):
                # Legacy list format: [{action_type: "...", ...}, ...]
                for entry in actions_section:
                    if isinstance(entry, dict):
                        atype = entry.get("action_type", "")
                        if atype:
                            self._config[atype] = entry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compose(self, signal: DomainSignal) -> ActionProposal | None:
        """Convert a domain signal to an ActionProposal.

        Returns None if:
          - No routing entry for this signal_type.
          - The mapped action_type is disabled in actions.yaml.
          - The signal's metadata is insufficient to build a proposal.
          - DEBT-012: The signal fails structural validation (logged and skipped).
        """
        # WorkIQ prose is display-only — do not parse for action proposals (prompt injection risk, F22/G6)
        # Signals MUST originate from deterministic skill outputs in state/work/, never from LLM inference.

        # DEBT-012: Validate signal — __post_init__ already fires at construction,
        # but catch defensive ValueError here for any deserialized / test-injected signals.
        try:
            if not signal.signal_type:
                raise ValueError("DomainSignal.signal_type must not be empty")
        except ValueError as exc:
            import sys  # noqa: PLC0415
            print(f"[ACTION_COMPOSER] Invalid signal skipped: {exc}", file=sys.stderr)
            return None

        # Find routing entry: prefer subtype (specific) over canonical signal_type
        _routing = _load_signal_routing()
        route = _routing.get(getattr(signal, "subtype", "") or "") or _routing.get(signal.signal_type)
        if route is None:
            return None

        action_type: str = route["action_type"]

        # Check action type is enabled in config
        action_cfg = self._config.get(action_type, {})
        if action_cfg.get("enabled") is False:
            return None

        # Compute effective friction (§10.3 rules 3, 4, 5)
        effective_friction = self._compute_friction(
            signal=signal,
            base_friction=route["friction"],
            action_cfg=action_cfg,
        )

        # Compute sensitivity
        sensitivity = self._compute_sensitivity(signal)

        # Build parameters from signal metadata
        params = self._build_parameters(action_type, signal)

        # Build title (≤120 chars)
        title = self._build_title(action_type, signal)[:120]

        # Build description
        description = self._build_description(action_type, signal)

        # Expiry: default 72h from now
        expires_at = _iso_offset_hours(_DEFAULT_EXPIRY_HOURS)

        proposal = ActionProposal(
            id=str(uuid.uuid4()),
            action_type=action_type,
            domain=signal.domain,
            title=title,
            description=description,
            parameters=params,
            friction=effective_friction,
            min_trust=route["min_trust"],
            sensitivity=sensitivity,
            reversible=route["reversible"],
            undo_window_sec=route["undo_window_sec"],
            expires_at=expires_at,
            source_step="12.5",
            source_skill=signal.source,
            linked_oi=signal.metadata.get("linked_oi"),
        )

        # RD-41: Validate proposal against Pydantic schema before returning.
        # Catches field type errors (wrong friction, invalid min_trust, etc.)
        # at compose time rather than at DB-insert time.
        try:
            from schemas.action import ActionProposalSchema as _APSchema  # noqa: PLC0415
            _APSchema.model_validate(proposal.__dict__)
        except Exception as _schema_exc:  # noqa: BLE001
            import sys as _sys  # noqa: PLC0415
            print(
                f"[ACTION_COMPOSER] Schema validation failed for {action_type} "
                f"signal {signal.signal_type!r}: {_schema_exc}",
                file=_sys.stderr,
            )
            # Audit log — best-effort (don't crash compose() if audit unavailable)
            try:
                from lib.logger import get_logger as _get_log  # noqa: PLC0415
                _get_log().error(
                    "PROPOSAL_SCHEMA_INVALID",
                    action_type=action_type,
                    signal_type=signal.signal_type,
                    error=str(_schema_exc)[:200],
                )
            except Exception:  # noqa: BLE001
                pass
            return None

        return proposal

    def compose_workflow(self, trigger: str, context: dict[str, Any]) -> list[ActionProposal]:
        """Generate a list of independently approvable proposals for a workflow.

        Supported triggers:
          - "address_change": notification emails + instruction sheet
          - "tax_prep": document request emails + summary instruction sheet

        Returns an empty list for unsupported triggers.
        """
        trigger = trigger.lower().strip()
        if trigger == "address_change":
            return self._workflow_address_change(context)
        if trigger == "tax_prep":
            return self._workflow_tax_prep(context)
        return []

    # ------------------------------------------------------------------
    # Friction & sensitivity computation
    # ------------------------------------------------------------------

    def _compute_friction(
        self,
        signal: DomainSignal,
        base_friction: str,
        action_cfg: dict[str, Any],
    ) -> str:
        """Return effective friction per §10.3 rules 3, 4, 5."""
        levels = [_FRICTION_ORDER[base_friction]]

        # Rule 4: cross-domain escalation
        if signal.domain in _HIGH_FRICTION_DOMAINS:
            levels.append(_FRICTION_ORDER["high"])

        # Rule 5: sensitivity-friction interaction
        sensitivity = self._compute_sensitivity(signal)
        if sensitivity in ("high", "critical"):
            levels.append(_FRICTION_ORDER["standard"])

        # Config base friction (from actions.yaml)
        cfg_friction = action_cfg.get("friction", "standard")
        if cfg_friction in _FRICTION_ORDER:
            levels.append(_FRICTION_ORDER[cfg_friction])

        # Rule 3: take the maximum
        return _FRICTION_NAMES[max(levels)]

    def _compute_sensitivity(self, signal: DomainSignal) -> str:
        """Infer sensitivity from signal domain and metadata."""
        # Immigration and medical are always high sensitivity
        if signal.domain in ("immigration", "health", "estate"):
            return "high"

        # Finance signals with amounts above threshold → high
        meta = signal.metadata
        if signal.domain == "finance":
            amount = meta.get("amount") or meta.get("amount_usd") or 0
            try:
                if float(amount) >= 500:
                    return "high"
            except (TypeError, ValueError):
                pass

        # High urgency → high sensitivity
        if signal.urgency >= 3 or signal.impact >= 3:
            return "high"

        return "standard"

    # ------------------------------------------------------------------
    # Parameter building
    # ------------------------------------------------------------------

    def _build_parameters(
        self,
        action_type: str,
        signal: DomainSignal,
    ) -> dict[str, Any]:
        """Build handler-specific parameters from signal metadata."""
        meta = signal.metadata.copy()

        if action_type == "email_send":
            return {
                "to": meta.get("recipient_email", meta.get("to", "")),
                "subject": meta.get("subject", f"Re: {signal.entity}"),
                "body": meta.get("body", meta.get("draft_body", "")),
                "cc": meta.get("cc", ""),
                "draft_first": True,
            }

        if action_type == "email_reply":
            return {
                "thread_id": meta.get("thread_id", ""),
                "body": meta.get("body", meta.get("draft_body", "")),
                "reply_all": meta.get("reply_all", False),
                "draft_first": True,
            }

        if action_type == "calendar_create":
            return {
                "summary": meta.get("summary", signal.entity),
                "start": meta.get("start", ""),
                "end": meta.get("end", ""),
                "location": meta.get("location", ""),
                "description": meta.get("description", signal.source),
                "calendar_id": meta.get("calendar_id", "primary"),
            }

        if action_type == "calendar_modify":
            return {
                "event_id": meta.get("event_id", ""),
                "calendar_id": meta.get("calendar_id", "primary"),
                "updates": meta.get("updates", {}),
            }

        if action_type == "reminder_create":
            return {
                "title": meta.get("title", signal.entity),
                "due_date": meta.get("due_date", ""),
                "list_name": meta.get("list_name", "Artha"),
                "priority": meta.get("priority", "P1"),
                "body": meta.get("body", ""),
                "reminder_datetime": meta.get("reminder_datetime", ""),
            }

        if action_type == "whatsapp_send":
            return {
                "phone_number": meta.get("phone_number", ""),
                "recipient_name": meta.get("recipient_name", signal.entity),
                "message": meta.get("message", meta.get("body", "")),
            }

        if action_type == "instruction_sheet":
            return {
                "task": meta.get("task", signal.signal_type.replace("_", " ").title()),
                "service": meta.get("service", signal.entity),
                "context": meta.get("context", {
                    "description": meta.get("description", ""),
                    "steps": meta.get("steps", []),
                    "notes": [f"Signal detected: {signal.signal_type}", f"Domain: {signal.domain}"],
                }),
            }

        if action_type == "todo_sync":
            return {"mode": meta.get("mode", "both")}

        # Fallback: pass all metadata as parameters
        return meta

    # ------------------------------------------------------------------
    # Title / description building
    # ------------------------------------------------------------------

    def _build_title(self, action_type: str, signal: DomainSignal) -> str:
        """Build a ≤120 char title for the approval UX."""
        entity = signal.entity
        meta = signal.metadata

        titles: dict[str, str] = {
            "email_send": f"Send email: {entity}",
            "email_reply": f"Reply: {meta.get('subject', entity)}",
            "calendar_create": f"Add to calendar: {entity}",
            "calendar_modify": f"Modify calendar event: {entity}",
            "reminder_create": f"Create reminder: {entity}",
            "whatsapp_send": f"WhatsApp Nudge: {entity}",
            "instruction_sheet": f"Generate guide: {signal.signal_type.replace('_', ' ')} — {entity}",
            "todo_sync": "Sync To Do lists",
        }
        return titles.get(action_type, f"{action_type}: {entity}")

    def _build_description(self, action_type: str, signal: DomainSignal) -> str:
        """Build a human-readable description for the proposal."""
        meta = signal.metadata
        lines = [
            f"Signal: {signal.signal_type} | Domain: {signal.domain}",
            f"Entity: {signal.entity}",
            f"Urgency: {signal.urgency}/3 | Impact: {signal.impact}/3",
            f"Source: {signal.source}",
        ]
        if meta.get("due_date") or meta.get("deadline"):
            lines.append(f"Due: {meta.get('due_date') or meta.get('deadline')}")
        if meta.get("amount") or meta.get("amount_usd"):
            lines.append(f"Amount: ${meta.get('amount') or meta.get('amount_usd')}")
        return " | ".join(lines[:3])  # Keep description concise

    # ------------------------------------------------------------------
    # Workflow builders
    # ------------------------------------------------------------------

    def _workflow_address_change(self, context: dict[str, Any]) -> list[ActionProposal]:
        """Generate proposals for an address change workflow."""
        proposals = []
        new_address = context.get("new_address", "")
        notify_list: list[dict] = context.get("notify_list", [])  # [{name, email, service}]

        for contact in notify_list:
            signal = DomainSignal(
                signal_type="address_update_notification",
                domain="general",
                entity=contact.get("name", ""),
                urgency=1,
                impact=2,
                source="workflow:address_change",
                metadata={
                    "to": contact.get("email", ""),
                    "subject": "Address Update Notification",
                    "body": context.get("email_body", f"Please update my address to: {new_address}"),
                    "draft_first": True,
                },
                detected_at=_now_iso(),
            )
            proposal = self.compose(signal)
            if proposal:
                proposals.append(proposal)

        # Add an instruction sheet for remaining services
        if context.get("remaining_services"):
            instruction_signal = DomainSignal(
                signal_type="address_update_notification",
                domain="general",
                entity="Address Change Master Guide",
                urgency=1,
                impact=2,
                source="workflow:address_change",
                metadata={
                    "task": "Address Change",
                    "service": "All Services",
                    "context": {
                        "description": f"Update address to: {new_address}",
                        "steps": context.get("remaining_services", []),
                    },
                },
                detected_at=_now_iso(),
            )
            proposal = self.compose(instruction_signal)
            if proposal:
                proposals.append(proposal)

        return proposals

    def _workflow_tax_prep(self, context: dict[str, Any]) -> list[ActionProposal]:
        """Generate proposals for a tax preparation workflow."""
        proposals = []
        tax_year = context.get("tax_year", datetime.now(timezone.utc).year - 1)
        cpa_email = context.get("cpa_email", "")

        if cpa_email:
            signal = DomainSignal(
                signal_type="appointment_needed",
                domain="finance",
                entity="CPA / Tax Advisor",
                urgency=2,
                impact=2,
                source="workflow:tax_prep",
                metadata={
                    "to": cpa_email,
                    "subject": f"Tax Filing {tax_year} — Schedule Appointment",
                    "body": context.get("cpa_email_body", f"Hi, I'd like to schedule our {tax_year} tax preparation appointment. Please let me know your availability."),
                    "draft_first": True,
                },
                detected_at=_now_iso(),
            )
            proposal = self.compose(signal)
            if proposal:
                proposals.append(proposal)

        # Instruction sheet for document checklist
        doc_signal = DomainSignal(
            signal_type="insurance_renewal",  # maps to instruction_sheet
            domain="finance",
            entity=f"Tax Prep {tax_year}",
            urgency=2,
            impact=2,
            source="workflow:tax_prep",
            metadata={
                "task": f"Tax Preparation {tax_year}",
                "service": context.get("tax_software", "TurboTax / H&R Block"),
                "context": {
                    "description": f"Checklist for {tax_year} tax filing",
                    "steps": context.get("document_checklist", [
                        "W-2 from all employers",
                        "1099 forms (interest, dividends, freelance)",
                        "Mortgage interest statement (Form 1098)",
                        "Property tax records",
                        "Charitable donation receipts",
                        "HSA contribution records (Form 5498-SA)",
                    ]),
                },
            },
            detected_at=_now_iso(),
        )
        proposal = self.compose(doc_signal)
        if proposal:
            proposals.append(proposal)

        return proposals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_offset_hours(hours: int) -> str:
    """Return ISO-8601 UTC timestamp n hours from now."""
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

def compose_from_signal(
    signal: DomainSignal,
    actions_config: dict[str, Any] | None = None,
) -> ActionProposal | None:
    """Convenience wrapper for single-signal composition."""
    return ActionComposer(actions_config).compose(signal)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_offset_hours(hours: int) -> str:
    """Return ISO-8601 UTC timestamp n hours from now."""
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
