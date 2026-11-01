"""Mandatory acceptance evidence: no synthetic outcome may stand in for measured release data."""
from __future__ import annotations

import hashlib
import json

import pytest

from catchup_core import policy
from catchup_core.contracts import CatchupRequest
from catchup_core.ledger import PermissionDenied
from catchup_core.policy import _validate_domains
from test_integrated_surfaces import release_scorecard


def verify(result):
    _validate_domains(result,['kids_school'])


def test_complete_acceptance_contract_passes():
    verify(release_scorecard())


@pytest.mark.parametrize('field',['corpus','document_fields'])
def test_missing_global_acceptance_evidence_refuses(field):
    result=release_scorecard()
    result.pop(field)
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize('bad',[0,18,19.0,True,21])
def test_completion_uses_exact_counts_and_requires_the_existing_95_percent_goal(bad):
    result=release_scorecard()
    result['measurements']['kids_school']['completed_runs']=bad
    with pytest.raises(PermissionDenied):
        verify(result)


def test_exact_95_percent_completion_passes():
    result=release_scorecard()
    result['measurements']['kids_school']['completed_runs']=19
    verify(result)


@pytest.mark.parametrize('scenario',['normal_cold','normal_warm','long_gap','provider_reset',
                                     'retention_1y','retention_2y','digest_limited'])
def test_missing_workload_scenario_refuses(scenario):
    result=release_scorecard()
    result['measurements']['kids_school']['workloads'].pop(scenario)
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize('seconds',[10,11,float('nan'),True])
def test_digest_latency_requires_the_actual_limited_path_below_ten_seconds(seconds):
    result=release_scorecard()
    result['measurements']['kids_school']['workloads']['digest_limited']['p95_seconds']=seconds
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize(('key','value'),[('held_out',False),('independently_labeled',False),
    ('development_end_at','2026-10-12T00:00:00Z'),('development_end_at','not-a-time'),
    ('development_thread_ids',['live']),('development_obligation_ids',['a'])])
def test_corpus_requires_independent_obligation_thread_and_time_holdout(key,value):
    result=release_scorecard()
    result['corpus'][key]=value
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize(('key','value'),[('split','development'),('thread_ids',[]),('obligation_ids',[]),
    ('latest_evidence_at','2026-10-13T00:00:00Z'),('as_of','2026-10-12T00:00:00')])
def test_case_requires_heldout_identity_and_no_future_source_evidence(key,value):
    result=release_scorecard()
    result['evaluations'][0][key]=value
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize('field',['dates','amounts','names'])
def test_each_consequential_document_field_must_meet_approved_accuracy_floor(field):
    result=release_scorecard()
    result['document_fields'][field]['correct']=98
    with pytest.raises(PermissionDenied):
        verify(result)


def test_document_false_confirmations_above_approved_half_percent_refuse():
    result=release_scorecard()
    result['document_fields']['false_confirmations']['false']=2
    with pytest.raises(PermissionDenied):
        verify(result)


def test_exact_document_floors_pass():
    result=release_scorecard()
    for field in ('dates','amounts','names'):
        result['document_fields'][field]['correct']=99
    result['document_fields']['false_confirmations']['false']=1
    verify(result)


@pytest.mark.parametrize('bad',[True,0,-1,1.0,None])
@pytest.mark.parametrize('field',['dates','false_confirmations'])
def test_document_denominators_require_positive_exact_counts(field,bad):
    result=release_scorecard()
    result['document_fields'][field]['total']=bad
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize('bad',[True,-1,101,None])
def test_document_numerators_require_exact_bounded_counts(bad):
    result=release_scorecard()
    result['document_fields']['dates']['correct']=bad
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize('kind',['missing-field','cohort','corpus','missing-corpus-id','missing-case-corpus'])
def test_document_and_case_evidence_must_bind_the_same_independent_cohort(kind):
    result=release_scorecard()
    if kind=='missing-field':
        result['document_fields'].pop('dates')
    elif kind=='cohort':
        result['document_fields']['cohort']={}
    elif kind=='corpus':
        result['document_fields']['corpus_id']='development'
    elif kind=='missing-corpus-id':
        result['corpus'].pop('id')
    else:
        result['evaluations'][0].pop('corpus_id')
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize(('scenario','key','value'),[
    ('normal_cold','passed',False),('normal_warm','cohort',{}),('normal_warm','runs',True),
    ('normal_cold','reference_machine',' '),('normal_warm','workload',{}),
    ('normal_cold','p95_seconds',181),('normal_warm','workload',{'signals':21}),
    ('long_gap','fair_progress',False),('long_gap','incomplete_interpretation_safe',False),
    ('long_gap','workload',{'signals':20,'gap_days':True}),
    ('provider_reset','verified_alerts_preserved',False),('provider_reset','coverage_degradation_reported',False),
    ('provider_reset','workload',{'signals':20}),('retention_1y','plaintext_fallback',True),
    ('retention_2y','restore_verified',False),('retention_1y','query_verified',False),
    ('retention_1y','workload',{'signals':20,'retention_years':True}),
    ('retention_2y','workload',{'signals':20,'retention_years':1}),
    ('digest_limited','fetch_skipped',False),('digest_limited','vault_skipped',False),
])
def test_workload_measurement_cannot_omit_its_required_safety_outcome(scenario,key,value):
    result=release_scorecard()
    result['measurements']['kids_school']['workloads'][scenario][key]=value
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize('bad',[None,{},[True],['same','same']])
def test_invalid_corpus_identifiers_refuse(bad):
    result=release_scorecard()
    result['corpus']['development_thread_ids']=bad
    with pytest.raises(PermissionDenied):
        verify(result)


def test_v3_artifacts_must_be_reevaluated_for_missing_acceptance_gates():
    result=release_scorecard()
    result['schema']='artha.release.v3'
    with pytest.raises(PermissionDenied,match='versioned'):
        verify(result)


@pytest.mark.parametrize('bad',[None,[],True])
def test_non_mapping_workload_measurements_refuse(bad):
    result=release_scorecard()
    result['measurements']['kids_school']=bad
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize('bad',[None,[],{}])
def test_missing_or_non_mapping_scenario_matrix_refuses(bad):
    result=release_scorecard()
    result['measurements']['kids_school']['workloads']=bad
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize('bad',[None,True,0])
def test_normal_completion_denominator_requires_nonzero_exact_count(bad):
    result=release_scorecard()
    result['measurements']['kids_school']['total_runs']=bad
    with pytest.raises(PermissionDenied):
        verify(result)


def test_missing_case_time_cannot_prove_a_temporal_holdout():
    result=release_scorecard()
    result['evaluations'][0]['as_of']=None
    with pytest.raises(PermissionDenied):
        verify(result)


@pytest.mark.parametrize('path', [
    (), ('bounds',), ('bounds', 'kids_school'), ('measurements',),
    ('measurements', 'kids_school', 'source_age_hours'), ('corpus',),
    ('document_fields',), ('document_fields', 'dates'), ('evaluations',),
    ('evaluations', 0), ('measurements', 'kids_school', 'workloads', 'normal_cold'),
    ('measurements', 'kids_school', 'workloads', 'normal_cold', 'workload'),
])
@pytest.mark.parametrize('bad', [None, True, 1, 'invalid', []])
def test_malformed_json_containers_refuse_with_permission_denied(path, bad):
    result = release_scorecard()
    if path:
        parent = result
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = bad
    else:
        result = bad
    with pytest.raises(PermissionDenied):
        verify(json.loads(json.dumps(result)))


@pytest.mark.parametrize('field', ['expected', 'surfaced', 'incumbent_surfaced'])
@pytest.mark.parametrize('bad', [None, True, 1, 'a', {}, [True], ['a', 'a']])
def test_malformed_obligation_labels_cannot_be_coerced_to_sets(field, bad):
    result = release_scorecard()
    result['evaluations'][0][field] = bad
    with pytest.raises(PermissionDenied):
        verify(json.loads(json.dumps(result)))


@pytest.mark.parametrize('field', ['quiet_claim', 'coverage_complete', 'citations_resolve',
                                  'consented_live', 'reviewed', 'source_negative_reviewed'])
@pytest.mark.parametrize('bad', [None, 0, 1, 'false', [], {}])
def test_case_flags_require_actual_json_booleans(field, bad):
    result = release_scorecard()
    result['evaluations'][0][field] = bad
    with pytest.raises(PermissionDenied):
        verify(json.loads(json.dumps(result)))


def test_duplicate_case_identity_refuses_even_when_all_outcomes_pass():
    result = release_scorecard()
    result['evaluations'][1]['case_id'] = result['evaluations'][0]['case_id']
    with pytest.raises(PermissionDenied):
        verify(json.loads(json.dumps(result)))


@pytest.fixture
def validate_serialized_scorecard(tmp_path, monkeypatch):
    """Reach the scorecard boundary without exercising preservation or live effects."""
    monkeypatch.setattr(policy, '_validate_preservation_evidence', lambda release: None)

    def validate(result, *, clearance=..., release_overrides=None):
        def approved(name, value):
            path = tmp_path / name
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            path.write_bytes(data)
            return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}

        release = {key: 'synthetic' for key in
                   ('owner_approved_at', 'rollback_snapshot', 'independent_copy', 'recovery_rehearsal')}
        release.update(authority_host='test-host', domains=['kids_school'],
                       stage1_clearance=approved('clearance.json',
                           {'passed': True, 'owner_signed_at': 'reviewed'} if clearance is ... else clearance),
                       scorecard=approved('scorecard.json', result))
        if release_overrides:
            release.update(release_overrides)
        policy.validate_release(CatchupRequest('cu_synthetic', 'owner', 'cli', host='test-host'),
                                {'cutover': True, 'release': release}, viewer='owner',
                                domains=['kids_school'], hostname='test-host',
                                cohort=result.get('cohort') if isinstance(result, dict) else {})
    return validate


@pytest.mark.parametrize('bad', [None, True, 1, 'invalid', [], {'evaluations': [None]}])
def test_outer_scorecard_parse_refuses_malformed_json(validate_serialized_scorecard, bad):
    with pytest.raises(PermissionDenied):
        validate_serialized_scorecard(bad)


def test_valid_v4_json_keeps_passing_without_mutating_evidence(validate_serialized_scorecard):
    result = json.loads(json.dumps(release_scorecard()))
    before = json.dumps(result, sort_keys=True)
    verify(result)
    validate_serialized_scorecard(result)
    assert json.dumps(result, sort_keys=True) == before


@pytest.mark.parametrize('bad', [None, True, 1, 'reviewed', []])
def test_stage1_clearance_requires_a_json_object(validate_serialized_scorecard, bad):
    with pytest.raises(PermissionDenied):
        validate_serialized_scorecard(release_scorecard(), clearance=bad)


@pytest.mark.parametrize('bad', [b'{', b'\xff', {'passed': True, 'owner_signed_at': True},
                               {'passed': True, 'owner_signed_at': ' '}])
def test_invalid_or_unreviewed_stage1_clearance_refuses(validate_serialized_scorecard, bad):
    with pytest.raises(PermissionDenied):
        validate_serialized_scorecard(release_scorecard(), clearance=bad)


@pytest.mark.parametrize('field', ['cases', 'live_cases'])
@pytest.mark.parametrize('bad', [None, True, '6', 6.0, -1, 0, 1, 7])
def test_outer_case_counts_require_exact_matching_integers(validate_serialized_scorecard, field, bad):
    result = release_scorecard()
    result[field] = bad
    with pytest.raises(PermissionDenied):
        validate_serialized_scorecard(result)


@pytest.mark.parametrize('bad', [None, True, '0.8', -1, 2, float('nan'), float('inf')])
def test_outer_precision_floor_requires_a_finite_probability(validate_serialized_scorecard, bad):
    with pytest.raises(PermissionDenied):
        validate_serialized_scorecard(release_scorecard(), release_overrides={'minimum_precision': bad})


@pytest.mark.parametrize('bad', [None, True, 1, ' '])
def test_scorecard_owner_review_requires_nonempty_text(validate_serialized_scorecard, bad):
    result = release_scorecard()
    result['owner_signed_at'] = bad
    with pytest.raises(PermissionDenied):
        validate_serialized_scorecard(result)


@pytest.mark.parametrize('bad', [None, True, 1, 'kids_school', {}, [None], [True], [1], [{}],
                               ['kids_school', 'kids_school']])
def test_release_domains_require_distinct_explicit_identifiers(validate_serialized_scorecard, bad):
    with pytest.raises(PermissionDenied):
        validate_serialized_scorecard(release_scorecard(), release_overrides={'domains': bad})


@pytest.mark.parametrize('bad', [True, 1, ' '])
def test_release_owner_approval_requires_nonempty_text(validate_serialized_scorecard, bad):
    with pytest.raises(PermissionDenied):
        validate_serialized_scorecard(release_scorecard(), release_overrides={'owner_approved_at': bad})
