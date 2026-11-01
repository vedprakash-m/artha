"""
scripts/lib/action_policy.py — deterministic action policy guard (re-artha RA-24, §6.16)
=========================================================================================
Runs in the executor path, outside any model, before an approved action may leave the machine.
Proposal checks are deterministic. Dispatch additionally reads one bounded policy revision and the local pause flag.
Four tiers (RA-24):

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
import hashlib
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

__all__ = ["HANDOFF_PATTERNS", "check_policy", "DispatchPolicy", "load_dispatch_policy",
           "authorize_dispatch_policy", "require_current_policy",
           "verify_action_preconditions", "require_action_preconditions"]

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
                 handoff_types: frozenset[str] = frozenset(),
                 named_recipients: frozenset[str] = frozenset()) -> tuple[bool, str]:
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
    sensitive = (str(getattr(proposal, "sensitivity", "standard")).lower() in _SENSITIVE
                 or getattr(proposal, "domain", "") == "health")
    if _OUTBOUND.match(action_type) and sensitive:
        recipients = _recipients(proposal.parameters if isinstance(proposal.parameters, Mapping) else {})
        if len(recipients) != 1 or _GROUPISH.search(recipients[0]):
            return False, "sensitive content needs exactly one named recipient (no groups, lists or broadcasts)"
        if recipients[0].strip().casefold() not in {r.casefold() for r in named_recipients}:
            return False, "sensitive recipient must be registered in the trusted action policy"
    return True, ""


@dataclass(frozen=True)
class DispatchPolicy:
    """The one policy revision authorizing a durable execution claim."""
    root: str
    sha256: str
    config: Mapping[str, Any]
    held: frozenset[str]
    handoff: frozenset[str]


def _policy_names(value: Any) -> frozenset[str]:
    if (not isinstance(value, list) or len(value) > 256
            or any(not isinstance(name, str) or not name.strip() or len(name) > 256 for name in value)):
        raise ValueError('Action policy needs bounded, explicit control lists.')
    return frozenset(value)


def _policy_bytes(root: Path) -> bytes:
    from catchup_core.session_lock import require_active_installation
    require_active_installation(root)
    if (root / 'config/actions.paused').exists():
        raise ValueError('Action execution is paused.')
    with (root / 'config/actions.yaml').open('rb') as handle:
        data = handle.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError('Action policy exceeds its size limit.')
    return data


def require_current_policy(root: str | Path, sha256: str) -> None:
    if hashlib.sha256(_policy_bytes(Path(root))).hexdigest() != sha256:
        raise ValueError('Action policy changed; review and approve again.')


def load_dispatch_policy(root: Path, action_type: str) -> DispatchPolicy:
    """Parse one bounded byte string, without truthy coercion or independently read policy lists."""
    import yaml
    data = _policy_bytes(root)
    try:
        document = yaml.safe_load(data)
    except (yaml.YAMLError, UnicodeError):
        raise ValueError('Action policy cannot be read.') from None
    if not isinstance(document, dict) or not isinstance(document.get('actions'), dict):
        raise ValueError('Action policy needs explicit actions.')
    config = document['actions'].get(action_type)
    controls = document.get('live_trial')
    if not isinstance(config, dict) or not isinstance(controls, dict):
        raise ValueError('Action policy needs explicit action and trial controls.')
    for key in ('enabled', 'autonomy_floor', 'preview_required', 'pii_check'):
        if key in config and type(config[key]) is not bool:
            raise ValueError('Action policy control flags must be booleans.')
    timeout = config.get('timeout_sec', 30)
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise ValueError('Action timeout must be an integer from 1 to 120 seconds.')
    limits = config.get('rate_limit', {})
    if (not isinstance(limits, dict) or set(limits) - {'max_per_hour', 'max_per_day'}
            or any(type(value) is not int or not 0 <= value <= 1_000_000 for value in limits.values())):
        raise ValueError('Action rate limits must be bounded nonnegative integers.')
    _policy_names(config.get('named_recipients', []))
    return DispatchPolicy(str(root.resolve()), hashlib.sha256(data).hexdigest(), config,
                          _policy_names(controls.get('held')), _policy_names(controls.get('handoff_only', [])))


def _resolve_ledger_path(root: Path, norm_pre: Mapping[str, Any]) -> Path:
    """Canonical namespace only; payload paths can constrain, never select authority."""
    import os
    base = Path(os.environ.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser().resolve()
    path = base / 'catchup_core/ledger.db'
    if base.is_relative_to(root.resolve()):
        raise ValueError('private evidence storage must be outside the installation')
    explicit = norm_pre.get('ledger_path')
    if explicit is not None and Path(explicit).resolve() != path:
        raise ValueError('action evidence ledger binding differs from the canonical namespace')
    return path


def verify_action_preconditions(
    proposal: Any,
    root: Path | None = None,
    *,
    raw_row: Mapping[str, Any] | None = None,
    provider_versions: Mapping[str, str] | None = None,
    ledger: Any = None,
    evidence_context: Any = None,
    evidence_queue: Any = None,
    evidence_head_reader: Any = None,
) -> tuple[bool, str]:
    """Verify Fact versions, subjects, permission generation, and provider versions for an action proposal."""
    import json  # noqa: PLC0415
    from catchup_core.actions import ActionError, normalize_preconditions, verify_evidence_preconditions  # noqa: PLC0415

    params = getattr(proposal, "parameters", None)
    raw_pre: Any = params.get("preconditions") if isinstance(params, Mapping) else None
    stored_pre: Any = None
    if raw_row is not None and raw_row.get("preconditions_json"):
        try:
            stored_pre = json.loads(str(raw_row["preconditions_json"]))
        except (ValueError, TypeError):
            return False, "action preconditions_json is malformed"
    if proposal is None:
        raw_pre = stored_pre
    is_core = (
        getattr(proposal, "source_skill", None) == "catchup_core"
        or (raw_row or {}).get("origin") == "catchup_core"
        or (raw_row or {}).get("source_skill") == "catchup_core"
        or (isinstance(raw_pre, Mapping) and bool(raw_pre.get("core_derived")))
    )
    if not is_core and not raw_pre and not stored_pre:
        return True, ""
    try:
        norm = normalize_preconditions(raw_pre, core_derived=is_core)
        if proposal is not None and raw_row is not None and 'preconditions_json' in raw_row:
            stored = normalize_preconditions(stored_pre, core_derived=is_core)
            compared = dict(norm)
            if 'ledger_path' in stored and 'ledger_path' not in compared:
                compared['ledger_path'] = stored['ledger_path']
            if stored != compared:
                raise ActionError('action evidence binding differs from the exact reviewed proposal')
            norm = stored
        if norm.get('provider_versions'):
            if root is None:
                raise ActionError('fresh provider versions require verification in the trusted action worker')
            from lib.action_evidence import require_provider_scope, require_provider_type
            if proposal is None:
                # Stored row at approval/claim: parameters may be sealed, but the reviewed bytes are
                # hash-bound and were checked in full at proposal and are checked again in the worker.
                require_provider_type(str((raw_row or {}).get('action_type') or ''))
            else:
                require_provider_scope(proposal)
        elif norm.get('core_derived') and proposal is not None:
            from lib.action_evidence import has_conditional_writer, require_provider_scope
            if has_conditional_writer(getattr(proposal, 'action_type', None)):
                require_provider_scope(proposal)
        if evidence_context is not None:
            from lib.action_evidence import verify_worker
            if root is None or evidence_queue is None:
                raise ActionError('missing scoped action evidence authority')
            verify_worker(proposal, root, norm, evidence_context, evidence_queue, evidence_head_reader)
        elif root is not None and not norm.get('core_derived'):
            verify_evidence_preconditions({**norm, 'provider_versions': {}})
        elif ledger is not None or not norm.get('core_derived'):
            verify_evidence_preconditions({**norm, 'provider_versions': {}}, ledger=ledger)
        elif root is not None:
            from lib.action_evidence import verify_parent
            verify_parent(proposal, root, norm)
        else:
            raise ActionError('core-derived actions require canonical evidence authority')
    except ActionError as exc:
        return False, str(exc)
    except Exception:
        return False, 'current evidence authorization is unavailable; review after local maintenance'
    return True, ""


def require_action_preconditions(
    proposal: Any,
    root: Path | None = None,
    *,
    raw_row: Mapping[str, Any] | None = None,
    provider_versions: Mapping[str, str] | None = None,
    ledger: Any = None,
    evidence_context: Any = None,
    evidence_queue: Any = None,
    evidence_head_reader: Any = None,
) -> None:
    ok, reason = verify_action_preconditions(
        proposal, root, raw_row=raw_row, provider_versions=provider_versions, ledger=ledger,
        evidence_context=evidence_context, evidence_queue=evidence_queue, evidence_head_reader=evidence_head_reader
    )
    if not ok:
        raise ValueError(reason)


def authorize_dispatch_policy(
    policy: DispatchPolicy,
    proposal: Any,
    approved_by: str,
    now: datetime,
    *,
    raw_row: Mapping[str, Any] | None = None,
    evidence_context: Any = None,
    evidence_queue: Any = None,
    evidence_head_reader: Any = None,
) -> None:
    if policy.config.get('enabled') is not True:
        raise ValueError('Action is disabled by current policy.')
    if '*' in policy.held or proposal.action_type in policy.held:
        raise ValueError('Action type is held by current policy.')
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise ValueError('Action needs an accountable approver.')
    allowed, reason = check_policy(proposal, approved_by, now, handoff_types=policy.handoff,
                                  named_recipients=frozenset(policy.config.get('named_recipients', [])))
    if not allowed:
        raise ValueError(reason)
    require_action_preconditions(proposal, Path(policy.root), raw_row=raw_row,
        evidence_context=evidence_context, evidence_queue=evidence_queue, evidence_head_reader=evidence_head_reader)
