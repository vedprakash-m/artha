"""Action evidence checks use a real isolated Ledger, never an alternate metadata file as authority."""
from contextlib import closing
from dataclasses import replace
import sqlite3

import pytest

from catchup_core.actions import ActionError, ActionPreconditions, ActionStore, ClaimRefused, normalize_preconditions, verify_evidence_preconditions
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ids import HmacKey
from catchup_core.ledger import FactInput, Ledger
from catchup_core.contracts import FactStatus

NOW = '2026-10-24T12:00:00Z'
FUTURE = '2026-10-24T13:00:00Z'


@pytest.fixture
def evidence(tmp_path):
    cipher = EnvelopeCipher(StaticKeyProvider({1: b'e' * 32}))
    with Ledger(tmp_path / 'ledger.db', id_key=HmacKey(1, b'i' * 32), cipher=cipher,
                owner_id='owner', consent_subjects=frozenset({'adult'}),
                known_subjects=frozenset({'kid', 'adult', 'owner'}), clock=lambda: NOW) as ledger:
        ledger.migrate()
        fact = FactInput('kids_school', 'deadline', 'synthetic', 'kid', NOW)
        fid = ledger.put_fact(fact)
        pre = {'core_derived': True, 'fact_versions': {fid: ledger.get_fact(fid, 'owner').recorded_at},
               'subjects': ['kid'], 'permission_generation': 0, 'fresh_until': FUTURE}
        yield ledger, fact, fid, pre


@pytest.mark.parametrize('change', [None, [], {'core_derived': 'false'}, {'core_derived': 1},
    {'permission_generation': True}, {'subjects': 'kid'}, {'fact_versions': {'x': None}},
    {'fact_versions': {1: 'stamp'}}, {'provider_versions': []}, {'fresh_until': 'naive'},
    {'fresh_until': '2026-10-24T12:00:00'}, {'unrecognized': 'value'}])
def test_strict_core_preconditions_refuse_invalid_shapes(evidence, change):
    pre = evidence[3]
    value = change if not isinstance(change, dict) else {**pre, **change}
    with pytest.raises(ActionError):
        normalize_preconditions(value, core_derived=True)


def test_core_preconditions_require_explicit_freshness(evidence):
    pre = dict(evidence[3])
    pre.pop('fresh_until')
    with pytest.raises(ActionError, match='fresh'):
        normalize_preconditions(pre)


def test_normalized_dataclass_and_json_contract_agree(evidence):
    ledger, _, fid, pre = evidence
    value = ActionPreconditions({fid: pre['fact_versions'][fid]}, ('kid',), 0, fresh_until=FUTURE)
    assert value.to_dict() == normalize_preconditions(pre)
    assert verify_evidence_preconditions(value, ledger=ledger, now=NOW) == normalize_preconditions(pre)


@pytest.mark.parametrize('source', ['path', 'connection', 'missing'])
def test_unverified_metadata_source_cannot_authorize_core_actions(evidence, source):
    ledger, _, _, pre = evidence
    supplied = {'path': ledger._path, 'connection': ledger.conn, 'missing': None}[source]
    with pytest.raises(ClaimRefused, match='verified Ledger'):
        verify_evidence_preconditions(pre, ledger=supplied, now=NOW)


@pytest.mark.parametrize('mutation', ['corrected', 'disputed', 'erased', 'scope', 'expired'])
def test_changed_or_inaccessible_evidence_refuses(evidence, mutation):
    ledger, fact, fid, pre = evidence
    if mutation == 'corrected':
        ledger.put_fact(replace(fact, value='changed'), fact_id=fid)
    elif mutation == 'disputed':
        ledger.set_fact_status(fid, FactStatus.DISPUTED)
    elif mutation == 'erased':
        ledger.conn.execute('DELETE FROM fact_versions WHERE fact_id=?', (fid,))
        ledger.conn.execute('DELETE FROM facts WHERE fact_id=?', (fid,))
    elif mutation == 'scope':
        pre = {**pre, 'subjects': ['owner']}
    else:
        pre = {**pre, 'fresh_until': NOW}
    with pytest.raises(ClaimRefused):
        verify_evidence_preconditions(pre, ledger=ledger, now=NOW)


def test_provider_version_requires_fresh_verifier_result(evidence):
    ledger, _, _, pre = evidence
    pre = {**pre, 'provider_versions': {'event/1': 'etag-1'}}
    for actual in (None, {}, {'event/1': 'etag-2'}):
        with pytest.raises(ClaimRefused, match='provider'):
            verify_evidence_preconditions(pre, ledger=ledger, provider_versions=actual, now=NOW)
    assert verify_evidence_preconditions(pre, ledger=ledger, provider_versions={'event/1': 'etag-1'}, now=NOW)


def test_adult_view_grant_does_not_authorize_export_or_expired_consent(evidence):
    ledger, _, _, pre = evidence
    consent = ledger.grant_consent('adult', 'adult', 'kids_school', ['view'], 'owner', expires_at=FUTURE)
    fact = FactInput('kids_school', 'deadline', 'synthetic adult', 'adult', NOW)
    fid = ledger.put_fact(fact)
    pre = {**pre, 'fact_versions': {fid: ledger.get_fact(fid, 'owner').recorded_at}, 'subjects': ['adult'],
           'permission_generation': ledger._permission_generation()}
    with pytest.raises(ClaimRefused, match='export'):
        verify_evidence_preconditions(pre, ledger=ledger, now=NOW)
    ledger.grant_consent('adult', 'adult', 'kids_school', ['export'], 'owner', expires_at=FUTURE)
    pre['permission_generation'] = ledger._permission_generation()
    assert verify_evidence_preconditions(pre, ledger=ledger, now=NOW)
    ledger.revoke_consent(consent)
    with pytest.raises(ClaimRefused):
        verify_evidence_preconditions(pre, ledger=ledger, now=NOW)


def test_review_and_atomic_claim_refuse_correction_without_reserving_operation(evidence, tmp_path):
    ledger, fact, fid, pre = evidence
    with closing(sqlite3.connect(tmp_path / 'actions.db')) as db:
        store = ActionStore(db, clock=lambda: NOW)
        digest = store.propose('p', operation_key='operation', payload={'title': 'synthetic'}, expires_at=FUTURE,
                               core_derived=True, preconditions=pre, ledger=ledger)
        store.approve('p', payload_hash=digest, approver='owner', nonce='review', ledger=ledger)
        ledger.put_fact(replace(fact, value='corrected'), fact_id=fid)
        with pytest.raises(ClaimRefused):
            store.claim('p', ledger=ledger)
        assert db.execute('SELECT COUNT(*) FROM operation_registry').fetchone()[0] == 0


@pytest.mark.parametrize('fields', [{'valid_to': '2026-10-24'}, {'valid_from': '2026-10-25'},
                                   {'validity_unknown': True}])
def test_temporally_unverified_fact_cannot_authorize_effect(evidence, fields):
    ledger, fact, fid, pre = evidence
    ledger.put_fact(replace(fact, **fields), fact_id=fid)
    pre = {**pre, 'fact_versions': {fid: ledger.get_fact(fid, 'owner').recorded_at}}
    with pytest.raises(ClaimRefused):
        verify_evidence_preconditions(pre, ledger=ledger, now=NOW)


def test_changed_preconditions_invalidate_old_review_hash_even_with_same_payload(evidence, tmp_path):
    ledger, _, _, pre = evidence
    with closing(sqlite3.connect(tmp_path / 'actions.db')) as db:
        store = ActionStore(db, clock=lambda: NOW)
        old = store.propose('p', operation_key='op', payload={'title': 'synthetic'}, expires_at=FUTURE,
                            preconditions=pre, ledger=ledger)
        changed = {**pre, 'fresh_until': '2026-10-24T12:30:00Z'}
        new = store.revise_payload('p', {'title': 'synthetic'}, preconditions=changed)
        assert old != new
        with pytest.raises(ActionError, match='approval'):
            store.approve('p', payload_hash=old, approver='owner', nonce='old', ledger=ledger)


def test_dispatch_rechecks_evidence_after_claim(evidence, tmp_path):
    ledger, fact, fid, pre = evidence
    with closing(sqlite3.connect(tmp_path / 'actions.db')) as db:
        store = ActionStore(db, clock=lambda: NOW)
        digest = store.propose('p', operation_key='op', payload={'title': 'synthetic'}, expires_at=FUTURE,
                               preconditions=pre, ledger=ledger)
        store.approve('p', payload_hash=digest, approver='owner', nonce='n', ledger=ledger)
        claim = store.claim('p', ledger=ledger)
        ledger.put_fact(replace(fact, value='changed'), fact_id=fid)
        with pytest.raises(ClaimRefused):
            store.mark_dispatched(claim, ledger=ledger)
        assert db.execute('SELECT state FROM operation_registry').fetchone()[0] == 'reserved'


@pytest.mark.parametrize('boundary', ['approve', 'claim', 'dispatch'])
def test_stored_evidence_cannot_change_without_new_review(evidence, tmp_path, boundary):
    import json
    ledger, _, _, pre = evidence
    with closing(sqlite3.connect(tmp_path / 'actions.db')) as db:
        store = ActionStore(db, clock=lambda: NOW)
        digest = store.propose('p', operation_key='op', payload={'title': 'synthetic'}, expires_at=FUTURE,
                               preconditions=pre, ledger=ledger)
        if boundary != 'approve':
            store.approve('p', payload_hash=digest, approver='owner', nonce='n', ledger=ledger)
        claim = store.claim('p', ledger=ledger) if boundary == 'dispatch' else None
        changed = {**pre, 'fresh_until': '2026-10-24T12:30:00Z'}
        db.execute('UPDATE action_proposals SET preconditions_json=?', (json.dumps(changed),))
        with pytest.raises(ActionError, match='review|binding'):
            if boundary == 'approve':
                store.approve('p', payload_hash=digest, approver='owner', nonce='n', ledger=ledger)
            elif boundary == 'claim':
                store.claim('p', ledger=ledger)
            else:
                store.mark_dispatched(claim, ledger=ledger)


def test_exact_proposal_and_persisted_preconditions_must_agree(evidence, monkeypatch):
    import json
    from types import SimpleNamespace
    from lib.action_policy import verify_action_preconditions
    monkeypatch.setattr('catchup_core.actions._now', lambda: NOW)
    ledger, _, _, pre = evidence
    proposal = SimpleNamespace(parameters={'preconditions': pre}, source_skill='catchup_core')
    stored = {**pre, 'subjects': ['owner']}
    allowed, reason = verify_action_preconditions(proposal, ledger=ledger,
        raw_row={'origin': 'catchup_core', 'preconditions_json': json.dumps(stored)})
    assert not allowed and 'binding' in reason
