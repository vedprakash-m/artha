"""RA-24: refused by code, not by a model's judgement."""
from __future__ import annotations

import sys
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from lib import action_policy  # noqa: E402
from lib.action_policy import check_policy  # noqa: E402
import test_action_evidence_dispatch as evidence_support  # noqa: E402

evidence_case = evidence_support.case

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def proposal(action="email_send", sensitivity="standard", expires=None, **params):
    expires = expires or (NOW + timedelta(hours=1)).isoformat()
    return SimpleNamespace(action_type=action, sensitivity=sensitivity, parameters=params, expires_at=expires)


def test_ordinary_approved_action_passes():
    assert check_policy(proposal(to="a@b.com"), "owner", NOW) == (True, "")


def test_hand_off_tier_is_never_executed_even_when_approved():
    for kind in ("payment_send", "bank_transfer", "password_reset", "delete_permanent_file", "legal_filing", "submit_tax"):
        ok, why = check_policy(proposal(kind), "owner", NOW)
        assert not ok and "hand-off only" in why, kind
    ok, why = check_policy(proposal("custom_thing"), "owner", NOW, handoff_types=frozenset({"custom_thing"}))
    assert not ok and "instruction sheet" in why


def test_expired_unreadable_or_stale_proposals_are_refused():
    assert not check_policy(proposal(expires="2026-10-20T11:00:00Z"), "owner", NOW)[0]
    assert "cannot be read" in check_policy(proposal(expires="soon"), "owner", NOW)[1]
    assert check_policy(proposal(expires="2026-10-20T13:00:00"), "owner", NOW)[0]            # naive means UTC


def test_standing_and_automatic_approvals_only_prepare():
    for who in ("standing:weekly", "system:executor", "auto"):
        ok, why = check_policy(proposal(to="a@b.com"), who, NOW)
        assert not ok and "preparation only" in why


def test_sensitive_content_needs_exactly_one_named_recipient():
    assert not check_policy(proposal(sensitivity="high", to="doc@clinic.org"), "owner", NOW)[0]
    assert check_policy(proposal(sensitivity="high", to="doc@clinic.org"), "owner", NOW,
                        named_recipients=frozenset({"doc@clinic.org"}))[0]
    for to in ("a@b.com, c@d.com", ["a@b.com", "c@d.com"], "all@school.org", "team@x.org", "", None):
        params = {"to": to} if to is not None else {}
        assert not check_policy(proposal(sensitivity="critical", **params), "owner", NOW)[0], to
    assert check_policy(proposal("reminder_create", sensitivity="critical"), "owner", NOW)[0]   # not outbound
    assert check_policy(proposal(sensitivity="standard", to="a@b.com, c@d.com"), "owner", NOW)[0]


@pytest.mark.parametrize('configured', [False, True])
def test_ledger_path_uses_only_the_canonical_external_namespace(tmp_path, monkeypatch, configured):
    root = tmp_path / 'installation'
    home = tmp_path / 'home'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.delenv('ARTHA_LOCAL_DIR', raising=False)
    base = home / '.artha-local'
    if configured:
        base = tmp_path / 'private'
        monkeypatch.setenv('ARTHA_LOCAL_DIR', str(base))
    expected = base / 'catchup_core/ledger.db'
    assert action_policy._resolve_ledger_path(root, {}) == expected
    assert action_policy._resolve_ledger_path(root, {'ledger_path': str(expected)}) == expected
    with pytest.raises(ValueError, match='differs from the canonical namespace'):
        action_policy._resolve_ledger_path(root, {'ledger_path': str(tmp_path / 'substitute.db')})
    assert not base.exists()


def test_ledger_path_refuses_storage_inside_the_installation(tmp_path, monkeypatch):
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'private'))
    with pytest.raises(ValueError, match='outside the installation'):
        action_policy._resolve_ledger_path(tmp_path, {})


@pytest.mark.parametrize('stored', ['{', 'not-json'])
def test_malformed_stored_preconditions_fail_closed(stored):
    assert action_policy.verify_action_preconditions(
        proposal(), raw_row={'preconditions_json': stored}) == (False, 'action preconditions_json is malformed')


def test_caller_supplied_provider_versions_cannot_authorize_dispatch():
    item = proposal(preconditions={'provider_versions': {'synthetic-resource': 'v2'}})
    for versions in (None, {'synthetic-resource': 'v1'}, {'synthetic-resource': 'v2'}):
        ok, reason = action_policy.verify_action_preconditions(item, provider_versions=versions)
        assert not ok and 'trusted action worker' in reason
    assert action_policy.verify_action_preconditions(None, raw_row={}) == (True, '')
    ok, reason = action_policy.verify_action_preconditions(None, raw_row={
        'preconditions_json': json.dumps(item.parameters['preconditions'])},
        provider_versions={'synthetic-resource': 'v2'})
    assert not ok and 'trusted action worker' in reason


def test_core_preconditions_require_canonical_or_explicit_ledger_authority(evidence_case):
    item = evidence_case.proposal
    ok, reason = action_policy.verify_action_preconditions(item)
    assert not ok and 'canonical evidence authority' in reason
    assert action_policy.verify_action_preconditions(item, ledger=evidence_case.ledger) == (True, '')


def test_real_parent_verifier_is_selected_with_only_its_execution_context_replaced(evidence_case, monkeypatch):
    from lib import action_evidence
    entered = []

    @contextmanager
    def authorized(root):
        assert root == evidence_case.root
        entered.append(root)
        yield evidence_case.ledger, evidence_case.config, None

    monkeypatch.setattr(action_evidence, 'authorized_ledger', authorized)
    assert action_policy.verify_action_preconditions(evidence_case.proposal, evidence_case.root) == (True, '')
    consent_id = evidence_case.ledger.conn.execute('SELECT consent_id FROM consent').fetchone()[0]
    evidence_case.ledger.revoke_consent(consent_id)
    ok, reason = action_policy.verify_action_preconditions(evidence_case.proposal, evidence_case.root)
    assert not ok and 'permission generation' in reason
    assert entered == [evidence_case.root, evidence_case.root]


@pytest.mark.parametrize('missing', ['root', 'queue'])
def test_worker_context_requires_both_scoped_authorities(evidence_case, missing):
    root = None if missing == 'root' else evidence_case.root
    queue = None if missing == 'queue' else object()
    ok, reason = action_policy.verify_action_preconditions(evidence_case.proposal, root,
        evidence_context={}, evidence_queue=queue)
    assert not ok and reason == 'missing scoped action evidence authority'


def test_worker_receives_normalized_exact_review_binding(evidence_case, monkeypatch):
    from lib import action_evidence
    from catchup_core.actions import normalize_preconditions
    pre = dict(evidence_case.pre, subjects=['adult', 'adult'])
    item = SimpleNamespace(parameters={'preconditions': pre}, source_skill='catchup_core')
    stored = dict(evidence_case.pre, ledger_path=str(evidence_case.ledger._path))
    context, queue, reader = object(), object(), object()
    calls = []

    def verify(received, root, normalized, scoped, connection, head_reader):
        assert received is item and root == evidence_case.root
        assert normalized == normalize_preconditions(stored)
        assert scoped is context and connection is queue and head_reader is reader
        calls.append(normalized)

    monkeypatch.setattr(action_evidence, 'verify_worker', verify)
    assert action_policy.verify_action_preconditions(item, evidence_case.root,
        raw_row={'preconditions_json': json.dumps(stored)}, evidence_context=context,
        evidence_queue=queue, evidence_head_reader=reader) == (True, '')
    assert len(calls) == 1
    stored['permission_generation'] += 1
    ok, reason = action_policy.verify_action_preconditions(item, evidence_case.root,
        raw_row={'preconditions_json': json.dumps(stored)}, evidence_context=context, evidence_queue=queue)
    assert not ok and 'differs from the exact reviewed proposal' in reason
    assert len(calls) == 1


@pytest.mark.parametrize('origin', [{'origin': 'catchup_core'}, {'source_skill': 'catchup_core'}])
def test_stored_core_origin_cannot_omit_evidence(origin):
    ok, reason = action_policy.verify_action_preconditions(proposal(), raw_row=origin)
    assert not ok and 'require bound evidence preconditions' in reason


def test_unavailable_parent_evidence_returns_only_a_sanitized_refusal(evidence_case, monkeypatch):
    from lib import action_evidence

    def unavailable(root):
        raise RuntimeError('synthetic-sensitive-diagnostic')

    monkeypatch.setattr(action_evidence, 'authorized_ledger', unavailable)
    ok, reason = action_policy.verify_action_preconditions(evidence_case.proposal, evidence_case.root)
    assert not ok and reason == 'current evidence authorization is unavailable; review after local maintenance'
    with pytest.raises(ValueError, match='current evidence authorization is unavailable'):
        action_policy.require_action_preconditions(evidence_case.proposal, evidence_case.root)


@pytest.mark.parametrize('approver', [None, '', '   ', 7])
def test_enabled_unheld_policy_requires_an_accountable_approver(tmp_path, approver):
    policy = action_policy.DispatchPolicy(str(tmp_path), 'synthetic', {'enabled': True}, frozenset(), frozenset())
    with pytest.raises(ValueError, match='accountable approver'):
        action_policy.authorize_dispatch_policy(policy, proposal(), approver, NOW)
