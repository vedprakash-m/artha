"""Provider pagination and raw metadata failures cannot become green release evidence."""
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from catchup_core import renewal_github as github
from catchup_core.renewal_ci import Gate
from catchup_core.renewal_store import RenewalError


def test_AR04_partial_listing_is_not_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(github, '_object', lambda _, **kwargs: {'workflow_runs': [], 'total_count': 1})
    with pytest.raises(RenewalError):
        github.collect(SimpleNamespace(root=tmp_path), 'example/project', 'a' * 40,
                       (Gate('.github/workflows/ci.yml', 'b' * 64, ('tests',)),), now=datetime.now(UTC))


def test_AR13_duplicate_provider_keys_refuse(monkeypatch):
    monkeypatch.setattr(github.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=b'{"total_count":0,"total_count":1}'))
    with pytest.raises(RenewalError):
        github._object('repos/example/project/actions/runs')


def test_AR04_missing_jobs_page_is_not_complete(tmp_path, monkeypatch):
    record = {'path': '.github/workflows/ci.yml', 'head_sha': 'a' * 40, 'head_branch': 'main',
              'head_repository': {'full_name': 'example/project'}, 'id': 1, 'run_attempt': 1}
    current = {**record, 'event': 'push', 'created_at': '2026-01-01T00:00:00Z',
               'status': 'completed', 'conclusion': 'success'}
    def get(path, **kwargs):
        if 'per_page=100' in path and '/jobs' not in path:
            return {'workflow_runs': [record], 'total_count': 1}
        if '/jobs' in path:
            return {'jobs': [], 'total_count': 1}
        return current
    monkeypatch.setattr(github, '_object', get)
    monkeypatch.setattr(github.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=b'workflow\n'))
    with pytest.raises(RenewalError):
        github.collect(SimpleNamespace(root=tmp_path), 'example/project', 'a' * 40,
                       (Gate('.github/workflows/ci.yml', 'b' * 64, ('tests',)),),
                       now=datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture
def provider(tmp_path, monkeypatch):
    import hashlib
    record = {'path': '.github/workflows/ci.yml', 'head_sha': 'a' * 40, 'head_branch': 'main',
              'head_repository': {'full_name': 'example/project'}, 'id': 1, 'run_attempt': 1}
    current = {**record, 'event': 'push', 'created_at': '2026-01-01T00:00:00Z',
               'status': 'completed', 'conclusion': 'success'}
    listing = {'workflow_runs': [record], 'total_count': 1}
    jobs = {'jobs': [{'run_id': 1, 'name': 'tests', 'status': 'completed', 'conclusion': 'success'}], 'total_count': 1}
    def get(path, **kwargs):
        if '/jobs' in path:
            return jobs
        if '/attempts/' in path:
            return {**current, 'run_attempt': int(path.rsplit('/', 1)[1])}
        return listing
    monkeypatch.setattr(github, '_object', get)
    monkeypatch.setattr(github.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=b'workflow\n'))
    def collect():
        return github.collect(SimpleNamespace(root=tmp_path), 'example/project', 'a' * 40,
                              (Gate(record['path'], hashlib.sha256(b'workflow\n').hexdigest(), ('tests',)),),
                              now=datetime(2026, 1, 1, tzinfo=UTC))
    return record, current, listing, jobs, collect


def test_AR04_complete_provider_evidence_passes_without_sensitive_fields(provider):
    _, _, _, _, collect = provider
    proof, runs = collect()
    assert proof['status'] == 'passed' and len(runs) == 1
    assert set(proof['runs'][0]) == {'id', 'attempt', 'workflow'}


@pytest.mark.parametrize('which,field,value', [('record', 'head_sha', 'wrong'), ('record', 'id', 0),
    ('record', 'run_attempt', 30), ('current', 'head_sha', 'wrong'), ('current', 'created_at', 'invalid'),
    ('listing', 'total_count', 101), ('jobs', 'total_count', 101), ('jobs', 'jobs', [{'run_id': 2}])])
def test_AR04_provider_identity_bounds_and_missing_data_refuse(provider, which, field, value):
    record, current, listing, jobs, collect = provider
    {'record': record, 'current': current, 'listing': listing, 'jobs': jobs}[which][field] = value
    with pytest.raises(RenewalError):
        collect()


def test_AR04_request_bound_is_not_silent_truncation(provider):
    record, _, _, _, collect = provider
    record['run_attempt'] = 16
    with pytest.raises(RenewalError, match='request bound'):
        collect()


@pytest.mark.parametrize('output,code', [(b'[]', 0), (b'bad', 0), (b'{}', 1), (b'x' * (1024*1024+1), 0)])
def test_AR13_provider_object_limits(monkeypatch, output, code):
    monkeypatch.setattr(github.subprocess, 'run', lambda *_a, **_k: SimpleNamespace(returncode=code, stdout=output))
    with pytest.raises(RenewalError):
        github._object('repos/example/project/actions/runs')


def test_AR13_provider_unavailable_is_bounded(monkeypatch):
    def unavailable(*_a, **_k):
        raise OSError('PRIVATE_CANARY')
    monkeypatch.setattr(github.subprocess, 'run', unavailable)
    with pytest.raises(RenewalError, match='^required GitHub evidence unavailable$'):
        github._object('repos/example/project/actions/runs')


def test_AR13_valid_provider_object_remains_data_only():
    result = github._object('repos/example/project/actions/runs', runner=lambda *_a, **_kw:
                            SimpleNamespace(returncode=0, stdout=b'{"total_count":0}'))
    assert result == {'total_count': 0}


def test_AR04_optional_workflow_does_not_replace_required_workflow(provider):
    record, _, listing, _, collect = provider
    listing['workflow_runs'].append({**record, 'path': '.github/workflows/optional.yml'})
    listing['total_count'] = 2
    assert collect()[0]['status'] == 'passed'


def test_AR13_invalid_budget_and_missing_committed_workflow_refuse(provider, monkeypatch, tmp_path):
    _, _, _, _, collect = provider
    with pytest.raises(RenewalError):
        github.collect(SimpleNamespace(root=tmp_path), 'example/project', 'a' * 40,
                       (Gate('.github/workflows/ci.yml', 'b' * 64, ('tests',)),), now=datetime.now(UTC), request_budget=0)
    monkeypatch.setattr(github.subprocess, 'run', lambda *_a, **_kw: SimpleNamespace(returncode=1, stdout=b''))
    with pytest.raises(RenewalError):
        collect()


def test_AR22_new_attempt_during_collection_cannot_hide_behind_old_success(provider, monkeypatch):
    from copy import deepcopy
    _, _, _, _, collect = provider
    original = github._object
    listings = [0]
    def race(path, **kwargs):
        value = original(path, **kwargs)
        if '/actions/runs?' in path:
            listings[0] += 1
            if listings[0] == 2:
                value = deepcopy(value)
                value['workflow_runs'][0]['run_attempt'] = 2
        return value
    monkeypatch.setattr(github, '_object', race)
    with pytest.raises(RenewalError):
        collect()
