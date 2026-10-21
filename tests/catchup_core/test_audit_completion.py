"""Counterexamples from the specification audit; synthetic, local and isolated."""
from __future__ import annotations

import json
from argparse import Namespace
from datetime import timedelta

import pytest

from catchup_core import coordinator as co, documents, ledger as lg
from catchup_core.adapters import fetch_via_handler
from catchup_core.backup import encrypted_snapshot, read_snapshot
from catchup_core.bundles import ProducerKey, export_bundle, import_bundles
from catchup_core.commands import execute
from catchup_core.extract import NormalizedSignal, parse_output
from catchup_core.goals import observe_goals
from catchup_core.incumbent import GuardedIncumbent
from catchup_core.legacy_state import LegacyStateAdapter
from catchup_core.scorecard import Case, score
from test_coordinator import CIPHER, NOW, PACK, cal, email, req
import test_coordinator


@pytest.fixture
def world(tmp_path):
    yield from test_coordinator.world.__wrapped__(tmp_path)


def test_restricted_alert_dates_never_persist_as_plaintext(world):
    led, _, make, _ = world
    co.run_catchup(req(), make({'school_email': email(), 'calendar': cal()}))
    raw = led.conn.execute('SELECT deadline,latest_useful_action FROM alerts').fetchone()
    assert tuple(raw) == (None, None)
    key = led.conn.execute('SELECT obligation_key FROM alerts').fetchone()[0]
    assert led.get_alert(key)['deadline'] == '2026-10-14'


def test_forget_removes_derived_goal_observation(world):
    led, _, make, _ = world
    co.run_catchup(req(), make({'school_email': email(), 'calendar': cal()}))
    goal = {'id': 'g', 'title': 'Synthetic', 'metric': {'kind': 'on_time_obligations',
        'domain': 'kids_school', 'predicate': 'exam_registration_deadline', 'target': 1}}
    observe_goals(led, [goal], 'ved', NOW + timedelta(days=5), required_sources=('school_email', 'calendar'))
    assert led.conn.execute('SELECT 1 FROM goal_observations').fetchone()
    led.forget('school_email', 'acct', 'm1')
    assert not led.conn.execute('SELECT 1 FROM goal_observations').fetchone()


def test_corroboration_does_not_rewrite_historical_knowledge(world):
    led, _, make, _ = world
    co.run_catchup(req(), make({'school_email': email(), 'calendar': cal()}))
    f = led.facts_matching('kids_school', 'exam_registration_deadline', 'kid1')[0]
    before = led.fact_history(f.fact_id, 'ved', f.recorded_at)
    sid, _ = led.upsert_signal(lg.SignalInput('school_email', 'acct', 'later', 'v1',
        (NOW + timedelta(days=1)).isoformat(), 'later-hmac', 'later-blob'))
    led.add_evidence(f.fact_id, sid, 0, 10)
    assert led.fact_history(f.fact_id, 'ved', f.recorded_at) == before
    assert len(led.get_fact(f.fact_id, 'ved').evidence) == 2


def test_complete_replacement_without_deadline_retracts_old_support(world):
    led, _, make, _ = world
    co.run_catchup(req('first'), make({'school_email': email(), 'calendar': cal()}))
    co.run_catchup(req('second'), make({'school_email': email((co.ConnectorRecord(
        'm1', 'Registration withdrawn; no deadline applies.', 'v2'),)), 'calendar': cal()}))
    assert not led.facts_matching('kids_school', 'exam_registration_deadline', 'kid1')


def test_interpretation_recovers_after_transient_model_failure(world):
    led, _, make, _ = world
    co.run_catchup(req('outage'), make({'school_email': email(), 'calendar': cal()}, extractor=lambda _: 'invalid'))
    result = co.run_catchup(req('recovered'), make({'school_email': email(), 'calendar': cal()}))
    assert result.artifact_ids and not led.unresolved_signals()


def test_forged_acknowledgment_cannot_close_missing_bundle_sequence(tmp_path):
    key = ProducerKey('secondary', b's' * 32)
    export_bundle(tmp_path, key, namespace='personal', recipient='authority', account_scope='school',
        authority_generation=1, sequence=2, records=[co.ConnectorRecord('m', 'synthetic')],
        requested_since=NOW.isoformat(), completed_until=NOW.isoformat(), created_at=NOW)
    (tmp_path / 'ack-forged.json').write_text(json.dumps({'data': {'producer': 'secondary',
        'scope': 'school', 'sequence': 1}, 'mac': 'invalid'}))
    result = import_bundles(tmp_path, {'secondary': key}, name='school_email', namespace='personal',
        recipient='authority', account_scope='school', authority_generation=1, now=NOW)
    assert result.fetch.status.value == 'degraded'


def test_signed_bundle_cannot_be_relabelled_as_another_connector(tmp_path):
    key = ProducerKey('secondary', b's' * 32)
    export_bundle(tmp_path, key, namespace='personal', recipient='authority', account_scope='school',
        authority_generation=1, sequence=1, records=[co.ConnectorRecord('m', 'synthetic')],
        requested_since=NOW.isoformat(), completed_until=NOW.isoformat(), created_at=NOW)
    result = import_bundles(tmp_path, {'secondary': key}, name='unrelated', namespace='personal',
        recipient='authority', account_scope='school', authority_generation=1, now=NOW)
    assert not result.fetch.records


def test_shadow_pause_does_not_change_live_workspace(world, tmp_path):
    _, _, make, _ = world
    with pytest.raises((ValueError, lg.LedgerError), match='shadow'):
        execute(Namespace(command='pause', shadow=True), make({}), tmp_path)
    assert not (tmp_path / 'config/actions.paused').exists()


@pytest.mark.parametrize('text', ['Accommodation is not approved.', 'Accommodation was previously approved.',
                                 'Accommodation may be approved.', 'No approved accommodation.'])
def test_ambiguous_or_negated_enum_is_not_verified(text):
    result = parse_output(json.dumps({'complete': True, 'candidates': [{'predicate': 'accommodation_status',
        'subject_id': 'kid1', 'value': 'approved', 'quote': text, 'span': [0, len(text)]}]}),
        PACK, NormalizedSignal('s', 'school_email', text), ['kid1'])
    assert not result.candidates and result.rejections


def test_partial_calendar_fetch_preserves_valid_cancellations():
    def records(**_):
        yield {'id': 'cancelled', 'status': 'cancelled'}
        yield {'id': 'malformed'}
    got = fetch_via_handler('calendar', 'acct', records, since=NOW.isoformat())
    assert got.status.value == 'degraded' and got.cancelled == ('cancelled',)


def test_nonpilot_review_obligation_prevents_quiet_claim(world, tmp_path):
    _, _, make, _ = world
    text = 'Home: property tax payment is due today.'
    def mixed(_):
        return json.dumps({'complete': True, 'candidates': [], 'other_obligations': [
            {'domain': 'home', 'subject_id': 'kid1', 'quote': text, 'span': [0, len(text)]}]})
    result = co.run_catchup(req(), make({'school_email': email((co.ConnectorRecord('mixed', text),)),
        'calendar': cal()}, extractor=mixed,
        incumbent=GuardedIncumbent(LegacyStateAdapter(tmp_path, ['kids_school'], NOW))))
    assert 'Nothing urgent' not in result.briefing_text
    assert result.status.value == 'degraded'


def test_document_identity_is_bound_to_subject(world, tmp_path, monkeypatch):
    led, _, _, _ = world
    monkeypatch.setattr(documents, 'extract_pdf', lambda _: {'pages': ['Synthetic notice'], 'complete': True})
    source = tmp_path / 'input.pdf'
    source.write_bytes(b'%PDF-synthetic')
    first = documents.ingest(source, 'kid1', led, 'ved', NOW)
    second = documents.ingest(source, 'kid2', led, 'ved', NOW)
    assert first != second


def test_snapshot_contains_original_documents_captures_and_replay(world, tmp_path):
    led, blobs, _, _ = world
    for name in ('documents/doc_123.ae1', 'inbox/cap_123.ae1', 'replay/fbk_123.ae1'):
        path = led._path.parent / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'AE1-synthetic-encrypted')
    target = tmp_path / 'backup.ae1'
    encrypted_snapshot(target, led._path, CIPHER, blobs=blobs._root)
    files = read_snapshot(target, CIPHER)
    assert {'documents/doc_123.ae1', 'inbox/cap_123.ae1', 'replay/fbk_123.ae1'} <= files.keys()


def test_negative_only_scorecard_is_not_sufficient_release_evidence():
    assert not score([Case('negative-only', frozenset(), frozenset())]).passes
