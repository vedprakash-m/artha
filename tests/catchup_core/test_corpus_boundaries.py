"""Permission, deletion and backup boundaries of the real-data evaluation store."""
from datetime import UTC, datetime
from contextlib import closing
import sqlite3

import pytest
from test_coordinator import KEY, CIPHER

from catchup_core import corpus, ledger as lg
from catchup_core.backup import encrypted_snapshot, read_snapshot

NOW = datetime(2026, 10, 21, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    with lg.Ledger(tmp_path / 'ledger.db', id_key=KEY, cipher=CIPHER,
                   consent_subjects=frozenset({'adult'}), known_subjects=frozenset({'owner', 'adult'})).open() as led:
        led.migrate()
        yield led


def signal(led, subject='owner', upstream='u1'):
    sid, _ = led.upsert_signal(lg.SignalInput('mail', 'private', upstream, 'v1', NOW.isoformat(), upstream, 'blob'))
    led.set_signal_subjects(sid, [subject])
    return sid


def listing(led):
    return corpus.prefill(led, lambda _: 'Synthetic private excerpt', 'mail', 'kids_school')


def test_corpus_and_coverage_withhold_revoked_source_and_unknown_provenance(store):
    sid = signal(store, 'adult')
    store.set_source_subjects('mail', 'private', ['adult'])
    store.set_coverage('mail', 'private', 'complete', completed_at=NOW.isoformat())
    assert listing(store) == [] and store.visible_coverage('owner') == []
    grant = store.grant_consent('adult', 'adult', 'mail:private', ['view'], 'owner')
    assert listing(store)[0]['signal_id'] == sid
    assert len(store.visible_coverage('owner')) == 1
    store.revoke_consent(grant)
    assert listing(store) == [] and store.visible_coverage('owner') == []
    unknown, _ = store.upsert_signal(lg.SignalInput('mail', 'private', 'unknown', 'v1', NOW.isoformat(), 'h2', 'blob'))
    with pytest.raises(lg.PermissionDenied):
        store.require_signal_operation(unknown, 'owner')


def test_owner_only_coverage_does_not_require_unrelated_adult_consent(store):
    signal(store)
    store.set_source_subjects('mail', 'private', ['owner'])
    store.set_coverage('mail', 'private', 'complete', completed_at=NOW.isoformat())
    assert len(store.visible_coverage('owner')) == 1


def test_corpus_label_erasure_is_journaled_and_cannot_reappear(store, tmp_path):
    signal(store)
    case = listing(store)[0]
    corpus.record_label(store, case, {'needs_alert': 'no'}, NOW)
    saved = (tmp_path / 'corpus' / f"{case['case_id']}.ae1").read_bytes()
    assert len(corpus.load_labels(store)) == 1
    store.forget('mail', 'private', 'u1')
    assert not (tmp_path / 'corpus' / f"{case['case_id']}.ae1").exists()
    (tmp_path / 'corpus' / f"{case['case_id']}.ae1").write_bytes(saved)
    assert corpus.load_labels(store) == []


def test_corpus_is_in_snapshot_and_permission_rechecked_on_label_read(store, tmp_path):
    signal(store, 'adult')
    grant = store.grant_consent('adult', 'adult', 'mail:private', ['view'], 'owner')
    case = listing(store)[0]
    corpus.record_label(store, case, {'needs_alert': 'no'}, NOW)
    encrypted_snapshot(tmp_path / 'snapshot.ae1', store._path, CIPHER)
    assert f"corpus/{case['case_id']}.ae1" in read_snapshot(tmp_path / 'snapshot.ae1', CIPHER)
    store.revoke_consent(grant)
    assert corpus.load_labels(store) == []
    with pytest.raises(lg.PermissionDenied):
        corpus.record_label(store, case, {'needs_alert': 'no'}, NOW)


def test_corpus_does_not_count_an_uncited_fact_as_a_surfaced_alert(store):
    sid = signal(store)
    fid = store.put_fact(lg.FactInput('kids_school', 'test_date', '2026-10-30', 'owner', NOW.isoformat(),
                                     evidence=((sid, 0, 4),)))
    assert listing(store)[0]['core_surfaced'] == []
    store.upsert_alert('obligation', domain='kids_school', primary_subject_id='owner', severity='P1', fact_ids=[fid])
    assert listing(store)[0]['core_surfaced'] == []  # an Alert row also needs returned-output evidence


def test_goal_label_requires_transition_and_status_agrees_with_release_cohort(store):
    signal(store)
    case = listing(store)[0]
    label = {'needs_alert': 'yes', 'severity': 'P2', 'expected': ['G1'], 'case_type': 'goal_correction'}
    with pytest.raises(corpus.CorpusError, match='before.*after'):
        corpus.record_label(store, case, label, NOW)
    record = corpus.record_label(store, case, dict(label, before='unknown', after='on_track'), NOW)
    assert record['before'] == 'unknown' and record['after'] == 'on_track'
    assert corpus.gaps([record])['goal_correction'] == 1
    assert corpus.gaps([dict(record, after='unknown')])['goal_correction'] == 0


def test_failed_physical_erasure_replays_the_same_corpus_identities(store, monkeypatch):
    signal(store)
    case = listing(store)[0]
    corpus.record_label(store, case, {'needs_alert': 'no'}, NOW)
    purge = store.purge_corpus
    monkeypatch.setattr(store, 'purge_corpus', lambda _: (_ for _ in ()).throw(OSError('interrupted')))
    with pytest.raises(OSError):
        store.forget('mail', 'private', 'u1')
    assert corpus.load_labels(store) == []
    monkeypatch.setattr(store, 'purge_corpus', purge)
    assert len(store.resume_pending_forgets()) == 1
    assert not (store._path.parent / 'corpus' / f"{case['case_id']}.ae1").exists()


def test_actual_returned_briefing_is_scored_separately_from_labels(tmp_path):
    from test_coordinator import world, email, cal, req
    from catchup_core import coordinator as co
    from catchup_core.contracts import ExecutionMode
    generator = world.__wrapped__(tmp_path)
    ledger, blobs, make, _ = next(generator)
    try:
        result = co.run_catchup(req('cu_evaluation', ExecutionMode.SHADOW), make({'school_email': email(), 'calendar': cal()}))
        case = corpus.prefill(ledger, blobs.get, 'school_email', 'kids_school')[0]
        assert len(case['core_surfaced']) == 1
        label = corpus.record_label(ledger, case, {'needs_alert': 'yes', 'severity': 'P1',
                                    'expected': case['core_surfaced']}, NOW)
        assert 'surfaced' not in label
        outcome = corpus.latest_outcome(ledger, 'cu_evaluation')
        assert outcome['versions']['pack'] == make({}).pack.version
        evaluated = corpus.evaluations([label], outcome)
        assert evaluated[0]['catchup_id'] == 'cu_evaluation'
        assert evaluated[0]['surfaced'] == label['expected']
        assert corpus.evaluations([dict(label, signal_id='not_evaluated')], outcome) == []
        assert corpus.latest_outcome(ledger, 'missing') is None
        assert result.briefing_text
    finally:
        generator.close()


def test_old_snapshot_privacy_replay_erases_corpus_and_legacy_restore_is_blocked(store, tmp_path):
    from catchup_core.backup import apply_privacy_history, restore_encrypted_snapshot
    signal(store)
    case = listing(store)[0]
    corpus.record_label(store, case, {'needs_alert': 'no'}, NOW)
    path = tmp_path / 'before.ae1'
    encrypted_snapshot(path, store._path, CIPHER)
    ciphertext = path.read_bytes()
    snapshot = read_snapshot(path, CIPHER)
    artifact = f"corpus/{case['case_id']}.ae1"
    assert artifact in snapshot and b'Synthetic private excerpt' not in ciphertext
    store.forget('mail', 'private', 'u1')
    assert not (tmp_path / artifact).exists()
    with closing(sqlite3.connect(':memory:')) as replay:
        replay.deserialize(snapshot['ledger.db'])
        removed = []
        apply_privacy_history(replay, removed.append, store._sealed.read(), store.privacy_history())
        assert artifact in removed
        assert replay.execute('SELECT count(*) FROM corpus_cases').fetchone()[0] == 0
    destination = tmp_path / 'restored'
    with pytest.raises(lg.LedgerLocked, match='governed checkpoint/quarantine'):
        restore_encrypted_snapshot(path, destination, CIPHER, store._sealed)
    assert not destination.exists() and path.read_bytes() == ciphertext


@pytest.mark.parametrize('field', ['signal', 'source'])
def test_unresolved_subject_inputs_fail_before_becoming_authority(store, field):
    sid = signal(store)
    with pytest.raises(ValueError, match='resolved'):
        if field == 'signal':
            store.set_signal_subjects(sid, [''])
        else:
            store.set_source_subjects('mail', 'private', [''])
    with pytest.raises(lg.LedgerError, match='identity'):
        store.purge_corpus(['../outside'])


def test_added_cosubject_cannot_be_relaxed_by_a_later_scope_resolution(store):
    sid = signal(store)
    store.set_signal_subjects(sid, ['adult'])
    store.set_signal_subjects(sid, ['owner'])
    assert listing(store) == []
    store.grant_consent('adult', 'adult', 'mail:private', ['view'], 'owner')
    assert len(listing(store)) == 1


@pytest.mark.parametrize('expected', ['one-key', [''], [3], [None]])
def test_bad_expected_keys_are_not_silently_reinterpreted(expected):
    with pytest.raises(corpus.CorpusError, match='bounded list'):
        corpus.validate({'needs_alert': 'yes', 'severity': 'P1', 'expected': expected})


def test_case_identity_and_corrupt_label_fail_closed(store, tmp_path):
    import json
    sid = signal(store)
    case = listing(store)[0]
    with pytest.raises(corpus.CorpusError, match='identity'):
        corpus.labeled(store, '../private')
    label = corpus.record_label(store, case, {'needs_alert': 'no'}, NOW)
    with pytest.raises(corpus.CorpusError, match='another source'):
        other = signal(store, upstream='u2')
        corpus.record_label(store, dict(case, signal_id=other), {'needs_alert': 'no'}, NOW)
    path = tmp_path / 'corpus' / f"{case['case_id']}.ae1"
    path.write_bytes(CIPHER.encrypt(json.dumps(dict(label, case_id='case_other')).encode(),
                                    aad=case['case_id'].encode())[0])
    with pytest.raises(corpus.CorpusError, match='envelope'):
        corpus.load_labels(store)
    assert sid


def test_corpus_export_checks_current_export_grant_and_payload_identity(store):
    from types import SimpleNamespace
    from catchup_core.commands import _authorize_export_artifact
    signal(store, 'adult')
    store.grant_consent('adult', 'adult', 'mail:private', ['view'], 'owner')
    case = listing(store)[0]
    corpus.record_label(store, case, {'needs_alert': 'no'}, NOW)
    path = store._path.parent / 'corpus' / f"{case['case_id']}.ae1"
    deps = SimpleNamespace(ledger=store, viewer='owner')
    with pytest.raises(lg.PermissionDenied):
        _authorize_export_artifact(deps, 'corpus/' + path.name, path.read_bytes())
    store.grant_consent('adult', 'adult', 'mail:private', ['export'], 'owner')
    _authorize_export_artifact(deps, 'corpus/' + path.name, path.read_bytes())
    with pytest.raises(lg.PermissionDenied):
        _authorize_export_artifact(deps, 'corpus/case_other.ae1', CIPHER.encrypt(b'{}', aad=b'case_other')[0])


def test_corpus_cli_evaluates_a_named_outcome_and_refuses_missing_results(monkeypatch):
    import json
    from types import SimpleNamespace
    from catchup_core import corpus_cli, runtime
    from contextlib import nullcontext
    from catchup_core import session_lock
    deps = SimpleNamespace(ledger=SimpleNamespace(_path=__import__('pathlib').Path('/unused/ledger.db'), close=lambda: None),
                           maintenance=nullcontext)
    monkeypatch.setattr(runtime, 'load_runtime_config', lambda _: SimpleNamespace(viewer='owner'))
    monkeypatch.setattr(runtime, 'build_deps', lambda *a, **kw: deps)
    monkeypatch.setattr(session_lock, 'session_lock', lambda _: nullcontext())
    monkeypatch.setattr(corpus, 'latest_outcome', lambda *a: None)
    args = corpus_cli._parse(['evaluate', '--catchup-id', 'cu_named'])
    with pytest.raises(corpus.CorpusError, match='no retained output'):
        corpus_cli._run(args)
    monkeypatch.setattr(corpus, 'latest_outcome', lambda *a: {'signals': [], 'alerts': []})
    monkeypatch.setattr(corpus, 'load_labels', lambda _: [])
    assert corpus_cli._run(args) == {'evaluations': []}
    label = corpus_cli._parse(['label', 'case_a', '--needs-alert', 'yes', '--type', 'goal_correction',
                              '--before', json.dumps('unknown'), '--after', json.dumps('on_track')])
    assert label.before == 'unknown' and label.after == 'on_track'
