"""HELDOUT-LABEL (specs/features/heldout-labeling.md): blind labels, field truth, declaration and evidence assembly."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from test_coordinator import CIPHER, KEY

from catchup_core import corpus, heldout, policy
from catchup_core import ledger as lg
from catchup_core.contracts import FactStatus, Provenance

NOW = datetime(2026, 10, 21, tzinfo=UTC)
CUTOFF = '2026-10-01T00:00:00+00:00'
AS_OF = '2026-10-28T00:00:00+00:00'
COHORT = {'implementation': 'x'}
TEXT = 'Your statement is $1,234.50, due 2026-11-03. Account holder: Asha Rao.'


@pytest.fixture
def led(tmp_path):
    with lg.Ledger(tmp_path / 'l.db', id_key=KEY, cipher=CIPHER, owner_id='ved').open() as ledger:
        ledger.migrate()
        yield ledger


def signal(led, upstream='u1', observed='2026-10-10T00:00:00+00:00'):
    sid, _ = led.upsert_signal(lg.SignalInput('gmail', 'acct', upstream, 'v1', observed, upstream, 'blob'))
    led.set_signal_subjects(sid, ['owner'])
    return sid


def case_of(led, sid, name='case_a', domain='finance', observed='2026-10-10T00:00:00+00:00'):
    return {'case_id': name, 'domain': domain, 'signal_id': sid, 'excerpt': TEXT[:40], 'observed_at': observed}


def fields(date='2026-11-03', amount='$1,234.50', person='Asha Rao'):
    return [{'kind': 'date', 'predicate': 'bill_due_date', 'value': date, 'span': '2026-11-03'},
            {'kind': 'amount', 'predicate': 'statement_balance', 'value': amount, 'span': '$1,234.50'},
            {'kind': 'name', 'predicate': 'account_holder', 'value': person, 'span': 'Asha Rao'}]


def put(led, sid, predicate, value, provenance=Provenance.VERIFIED_SOURCE):
    led.put_fact(lg.FactInput(domain='finance', predicate=predicate, value=value, primary_subject_id='owner',
                              observed_at='t', status=FactStatus.CURRENT, provenance=provenance,
                              evidence=((sid, 0, 4),)))


def label(led, sid, name='case_a', **extra):
    body = {'needs_alert': 'no', 'fields': fields(), 'thread_ids': [name], **extra}
    return corpus.record_label(led, case_of(led, sid, name), body, NOW, source_text=TEXT)


def declare(led, **kw):
    args = {'corpus_id': 'heldout-1', 'development_end_at': CUTOFF, 'development_obligation_ids': ['dev-ob'],
            'development_thread_ids': ['dev-thread']}
    return heldout.declare(led, **{**args, **kw}, now=NOW)


# HL-02 -------------------------------------------------------------------------------------------------------------

def test_a_field_needs_a_verbatim_span_from_the_source(led):
    sid = signal(led)
    for bad in ({'span': ''}, {'span': 'not in the message'}, {'kind': 'phone'}, {'value': ''}):
        broken = [{**fields()[0], **bad}]
        with pytest.raises(corpus.CorpusError):
            corpus.record_label(led, case_of(led, sid), {'needs_alert': 'no', 'fields': broken}, NOW,
                                source_text=TEXT)
    with pytest.raises(corpus.CorpusError, match='source text'):
        corpus.record_label(led, case_of(led, sid), {'needs_alert': 'no', 'fields': fields()}, NOW)


def test_field_truth_is_stored_encrypted_and_reloaded_and_v2_labels_still_load(led, tmp_path):
    sid, other = signal(led), signal(led, 'u2')
    label(led, sid)
    corpus.record_label(led, case_of(led, other, 'case_b'), {'needs_alert': 'no'}, NOW)
    loaded = {item['case_id']: item for item in corpus.load_labels(led)}
    assert [f['kind'] for f in loaded['case_a']['fields']] == ['date', 'amount', 'name']
    assert loaded['case_b'].get('fields', []) == []
    assert b'Asha' not in (tmp_path / 'corpus' / 'case_a.ae1').read_bytes()


# HL-01 -------------------------------------------------------------------------------------------------------------

def test_the_blind_view_shows_source_text_and_hides_the_core_output():
    shown = corpus.blind_case({'case_id': 'case_a', 'domain': 'finance', 'signal_id': 's', 'observed_at': 't',
                               'title': 'T', 'excerpt': 'ex', 'core_surfaced': ['k'], 'labeled': True,
                               'evaluation_catchup': 'cu_1'}, TEXT)
    assert shown == {'case_id': 'case_a', 'domain': 'finance', 'observed_at': 't', 'text': TEXT}


# HL-10 -------------------------------------------------------------------------------------------------------------

def test_relabeling_keeps_history_and_only_the_latest_counts(led):
    sid = signal(led)
    label(led, sid)
    corpus.record_label(led, case_of(led, sid), {'needs_alert': 'yes', 'severity': 'P1', 'expected': ['k'],
                                                 'fields': fields(), 'thread_ids': ['case_a']}, NOW, source_text=TEXT)
    [current] = corpus.load_labels(led)
    assert current['needs_alert'] == 'yes' and len(current['history']) == 1
    assert current['history'][0]['needs_alert'] == 'no' and 'history' not in current['history'][0]


# HL-04 / declaration -----------------------------------------------------------------------------------------------

def test_the_declaration_is_encrypted_write_once_and_needs_dated_evidence(led, tmp_path):
    assert heldout.load_declaration(led) is None
    saved = declare(led)
    assert saved['independence_basis'] == 'blind-owner' and saved['declared_by'] == 'ved'
    assert heldout.load_declaration(led) == saved
    assert b'heldout-1' not in (tmp_path / 'corpus' / 'declaration.ae1').read_bytes()
    with pytest.raises(corpus.CorpusError, match='already'):
        declare(led, corpus_id='other')
    with pytest.raises(corpus.CorpusError, match='timezone'):
        heldout.declare(led, corpus_id='x', development_end_at='2026-10-01T00:00:00',
                        development_obligation_ids=[], development_thread_ids=[], now=NOW)


# HL-03 / D-5 -------------------------------------------------------------------------------------------------------

def test_normalization_is_exact_but_ignores_formatting():
    assert heldout.normalize('amount', '$1,234.50') == heldout.normalize('amount', '1234.5') == '1234.50'
    assert heldout.normalize('amount', '1,234.51') != '1234.50'
    assert heldout.normalize('date', '2026-11-03') == heldout.normalize('date', '2026-11-03T00:00:00Z')
    assert heldout.normalize('name', '  asha   RAO ') == 'asha rao'


def test_scoring_counts_correct_fields_and_false_confirmations_from_verified_facts(led):
    sid = signal(led)
    label(led, sid)
    put(led, sid, 'bill_due_date', '2026-11-03')                       # correct, verified
    put(led, sid, 'statement_balance', '$1,999.00')                    # wrong, verified -> false confirmation
    # account_holder missing entirely -> counts against accuracy only
    score = heldout.score_fields(led, corpus.load_labels(led), 'ved')
    assert score['dates'] == {'correct': 1, 'total': 1}
    assert score['amounts'] == {'correct': 0, 'total': 1}
    assert score['names'] == {'correct': 0, 'total': 1}
    assert score['false_confirmations'] == {'false': 1, 'total': 2}


def test_an_unverified_wrong_value_is_not_a_false_confirmation(led):
    sid = signal(led)
    label(led, sid)
    put(led, sid, 'statement_balance', '$1,999.00', Provenance.MODEL_HYPOTHESIS)
    score = heldout.score_fields(led, corpus.load_labels(led), 'ved')
    assert score['amounts'] == {'correct': 0, 'total': 1}
    assert score['false_confirmations'] == {'false': 0, 'total': 0}


def test_unsure_labels_contribute_no_field_truth(led):
    sid = signal(led)
    corpus.record_label(led, case_of(led, sid), {'needs_alert': 'unsure', 'fields': fields(),
                                                 'thread_ids': ['case_a']}, NOW, source_text=TEXT)
    score = heldout.score_fields(led, corpus.load_labels(led), 'ved')
    assert score['dates'] == {'correct': 0, 'total': 0}


# HL-06 / HL-07 / HL-05 ---------------------------------------------------------------------------------------------

def outcome_for(sid):
    return {'catchup_id': 'cu_1', 'cohort': COHORT, 'versions': {}, 'signals': [sid], 'alerts': [],
            'quiet_claim': False, 'coverage_complete': True, 'citations_resolve': True}


def assemble(led, **kw):
    sid = kw.pop('sid')
    return heldout.assemble(led, corpus.load_labels(led), outcome_for(sid), as_of=AS_OF, **kw)


def test_assembled_evidence_passes_the_unchanged_release_validators(led):
    sid = signal(led)
    label(led, sid)
    put(led, sid, 'bill_due_date', '2026-11-03')
    put(led, sid, 'statement_balance', '1234.50')
    put(led, sid, 'account_holder', 'asha rao')
    declare(led)
    evidence = assemble(led, sid=sid, min_fields=1)
    result = {'cohort': COHORT, **evidence}
    policy._validate_acceptance_corpus(result)
    policy._validate_document_accuracy(result)
    assert evidence['corpus']['independently_labeled'] and evidence['corpus']['held_out']
    assert evidence['corpus']['basis'] == 'blind-owner' and evidence['corpus']['labeled_by'] == 'ved'
    assert evidence['evaluations'][0]['split'] == 'held_out'
    assert evidence['evaluations'][0]['obligation_ids'] == [] and evidence['evaluations'][0]['thread_ids'] == ['case_a']


def test_assembly_refuses_below_the_field_floor_and_says_what_is_missing(led):
    sid = signal(led)
    label(led, sid)
    declare(led)
    with pytest.raises(corpus.CorpusError, match=r'dates 1/100'):
        assemble(led, sid=sid)


def test_assembly_refuses_without_a_declaration(led):
    sid = signal(led)
    label(led, sid)
    with pytest.raises(corpus.CorpusError, match='declaration'):
        assemble(led, sid=sid, min_fields=1)


def test_a_case_on_or_before_the_cutoff_is_not_held_out(led):
    sid = signal(led, observed='2026-09-30T00:00:00+00:00')
    corpus.record_label(led, case_of(led, sid, observed='2026-09-30T00:00:00+00:00'),
                        {'needs_alert': 'no', 'fields': fields(), 'thread_ids': ['case_a']}, NOW, source_text=TEXT)
    declare(led)
    with pytest.raises(corpus.CorpusError, match='no held-out cases'):
        assemble(led, sid=sid, min_fields=1)


def test_a_thread_or_obligation_overlap_with_development_refuses_naming_cases_only(led):
    sid = signal(led)
    label(led, sid, thread_ids=['dev-thread'])
    declare(led)
    with pytest.raises(corpus.CorpusError) as error:
        assemble(led, sid=sid, min_fields=1)
    assert 'case_a' in str(error.value) and 'dev-thread' not in str(error.value)


def test_status_reports_the_missing_counts_and_writes_nothing(led):
    sid = signal(led)
    label(led, sid)
    report = heldout.status(led, corpus.load_labels(led), min_fields=100)
    assert report['declared'] is False
    assert report['fields']['dates'] == 1 and report['fields_missing'] == {'dates': 99, 'amounts': 99, 'names': 99}
    assert any('declaration' in item for item in report['missing_for_gate'])
    declare(led)
    assert heldout.status(led, corpus.load_labels(led), min_fields=100)['declared'] is True


# CLI ---------------------------------------------------------------------------------------------------------------

def test_the_cli_shows_source_only_labels_with_fields_declares_and_refuses_a_thin_assembly(led, tmp_path, monkeypatch):
    import json  # noqa: PLC0415
    from contextlib import nullcontext  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    from catchup_core import corpus_cli, runtime  # noqa: PLC0415
    sid = signal(led)
    deps = SimpleNamespace(ledger=led, blobs=SimpleNamespace(get=lambda ref: TEXT), maintenance=nullcontext)
    monkeypatch.setattr(runtime, "load_runtime_config", lambda path: SimpleNamespace(viewer="ved"))
    monkeypatch.setattr(runtime, "build_deps", lambda request, **kw: deps)
    led.close = lambda: None

    def run(*argv):
        return corpus_cli._run(corpus_cli._parse(list(argv)))
    shown = run("next", "--source", "gmail", "--domain", "finance")
    assert set(shown) == {"remaining", "case_id", "domain", "observed_at", "text"} and shown["text"] == TEXT
    field = json.dumps(fields()[0])
    case_id = shown["case_id"]
    done = run("label", case_id, "--needs-alert", "no", "--field", field, "--source", "gmail", "--domain", "finance")
    assert done["reviewed"] is True and run("next", "--source", "gmail", "--domain", "finance") == {"remaining": 0}
    assert run("status")["fields"]["dates"] == 1
    run("declare", "--id", "heldout-1", "--development-end", CUTOFF)
    with pytest.raises(corpus.CorpusError, match="Catch-up has no retained"):
        run("assemble", "--catchup-id", "cu_x", "--as-of", AS_OF, "--out", str(tmp_path / "evidence.json"))
    assert sid and not (tmp_path / "evidence.json").exists()
