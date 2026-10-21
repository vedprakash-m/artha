"""
scripts/lib/action_policy.py — deterministic action policy guard (re-artha RA-24, §6.16)
=========================================================================================
Runs in the executor path, outside any model, before an approved action may leave the machine.
Pure functions over the proposal; no I/O, so it is trivially testable and cannot be talked out of
a decision.  Four tiers (RA-24):

1. machine-maintained reversible internal writes — not handled here (they never reach the executor);
2. edits to curated State Files — diff approval, not handled here;
3. external effects — exact-payload approval (RA-05), plus the checks below;
4. **hand-off only** — password changes, money movement, legal submissions and permanent deletion of
   non-Artha data: Artha prepares an instruction sheet and never executes.

Checks (first failure wins): hand-off tier, expiry, standing approvals (preparation only in the pilot),
recipient class versus data sensitivity (health or critical content needs one named recipient).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

__all__ = ["HANDOFF_PATTERNS", "check_policy"]

HANDOFF_PATTERNS = re.compile(
    r"(payment|pay_|transfer|wire|withdraw|deposit|trade|purchase|checkout|password|credential|2fa|"
    r"delete_permanent|permanent_delete|purge|legal|filing|petition|submit_tax)", re.IGNORECASE)
_OUTBOUND = re.compile(r"^(email|whatsapp|slack|sms|message)_", re.IGNORECASE)
_RECIPIENT_KEYS = ("to", "recipient", "recipients", "channel", "phone")
_GROUPISH = re.compile(r"(all@|everyone|team@|group|list|distribution|@channel|@here|\*)", re.IGNORECASE)
_SENSITIVE = {"high", "critical"}


def _as_utc(stamp: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _recipients(parameters: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in _RECIPIENT_KEYS:
        raw = parameters.get(key)
        if isinstance(raw, str) and raw.strip():
            values.extend(p for p in re.split(r"[;,]", raw) if p.strip())
        elif isinstance(raw, list | tuple):
            values.extend(str(p) for p in raw if str(p).strip())
    return values


def check_policy(proposal: Any, approved_by: str, now: datetime, *,
                 handoff_types: frozenset[str] = frozenset()) -> tuple[bool, str]:
    """``(allowed, reason)``.  ``proposal`` needs action_type, sensitivity, parameters and expires_at."""
    action_type = str(proposal.action_type)
    if action_type in handoff_types or HANDOFF_PATTERNS.search(action_type):
        return False, (f"{action_type} is hand-off only (money, credentials, legal or permanent deletion): "
                       "Artha prepares an instruction sheet and does not execute it")
    expires = getattr(proposal, "expires_at", None)
    if expires:
        deadline = _as_utc(str(expires))
        if deadline is None:
            return False, "the proposal's expiry cannot be read, so it is treated as expired"
        if deadline <= now.astimezone(timezone.utc):
            return False, "the proposal has expired; propose it again to approve a fresh payload"
    if approved_by.startswith(("standing:", "system:", "auto")):
        return False, "standing and automatic approvals authorize preparation only; a person must approve execution"
    if _OUTBOUND.match(action_type) and str(getattr(proposal, "sensitivity", "standard")).lower() in _SENSITIVE:
        recipients = _recipients(proposal.parameters if isinstance(proposal.parameters, Mapping) else {})
        if len(recipients) != 1 or _GROUPISH.search(recipients[0]):
            return False, "sensitive content needs exactly one named recipient (no groups, lists or broadcasts)"
    return True, ""
