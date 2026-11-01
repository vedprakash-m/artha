"""Work cache consumers use synthetic storage/providers only."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
import sys

import pytest


def test_query_scope_and_depth_never_share_cache_keys():
    from connectors.workiq_bridge import _cache_key_for_mode as key
    base = key('email', lookback='24', context_depth='brief')
    assert base != key('email', lookback='48', context_depth='brief')
    assert base != key('email', lookback='24', context_depth='rich')
    assert key('teams_ai', channels=['a']) != key('teams_ai', channels=['b'])
    assert key('people', person_name='a b') != key('people', person_name='a_b')


def test_future_and_naive_cache_times_are_not_fresh():
    from connectors.workiq_bridge import _get_cached
    for timestamp in [(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), '2026-10-24T00:00:00']:
        assert _get_cached('email', 'key', {'key': {'cached_at': timestamp, 'data': [{'x': 1}]}}) is None


@pytest.mark.parametrize('mode', ['calendar', 'email', 'teams'])
def test_rich_queries_render_and_cache_failure_keeps_fresh_positives(mode, capsys):
    from connectors import workiq_bridge as bridge
    from catchup_core.work_cache import WorkCacheBlocked
    raw = {'calendar': '2026-10-24 | 09:00 | 10:00 | Example | Alice | Office | no',
           'email': 'alice@example.invalid | Example | 2026-10-24 | yes',
           'teams': 'Alice | Channel | Example | yes'}[mode]
    with (patch.object(bridge, '_load_cache', return_value={}),
          patch.object(bridge, '_save_cache', side_effect=WorkCacheBlocked('sensitive backend detail')),
          patch.object(bridge, '_ask_workiq', side_effect=['synthetic private narrative', raw])):
        records = list(bridge.fetch(mode=mode, auth_context={}, context_depth='rich'))
    assert records
    output = capsys.readouterr().err
    assert 'uncached' in output
    assert 'sensitive backend detail' not in output
    assert records[0]['processing_gaps'] == ['work_cache_persistence_failed']


def test_legacy_block_prevents_provider_query(capsys):
    from connectors import workiq_bridge as bridge
    from catchup_core.work_cache import WorkCacheBlocked
    with (patch.object(bridge, '_load_cache', side_effect=WorkCacheBlocked('legacy private')),
          patch.object(bridge, '_ask_workiq') as provider):
        with pytest.raises(RuntimeError, match='encrypted Work cache unavailable'):
            list(bridge.fetch(mode='email', auth_context={}))
    provider.assert_not_called()
    assert 'legacy private' not in capsys.readouterr().err


def test_health_receipt_contains_no_probe_identity_and_validates_shape(tmp_path):
    from lib.workiq_health import read_receipt, write_receipt
    path = tmp_path / 'health.json'
    write_receipt(path, available=True, auth_valid=True)
    raw = json.loads(path.read_text())
    assert set(raw) == {'available', 'auth_valid', 'platform', 'checked_at'}
    assert read_receipt(path, max_age=86400)['available'] is True
    raw['user_name'] = 'private identity'
    path.write_text(json.dumps(raw))
    assert read_receipt(path, max_age=86400) is None


@pytest.mark.parametrize('timestamp', ['naive', 'future', 'old'])
def test_health_receipt_refuses_invalid_time(tmp_path, timestamp):
    from lib.workiq_health import read_receipt
    now = datetime.now(timezone.utc)
    stamps = {'naive': '2026-10-24T12:00:00', 'future': (now + timedelta(days=2)).isoformat(),
              'old': (now - timedelta(days=2)).isoformat()}
    path = tmp_path / 'health.json'
    path.write_text(json.dumps({'available': True, 'auth_valid': True, 'platform': 'Windows',
                                'checked_at': stamps[timestamp]}))
    assert read_receipt(path, max_age=86400) is None


def test_preflight_never_persists_probe_stdout(tmp_path, monkeypatch):
    from preflight import integration_checks as checks
    from types import SimpleNamespace
    path = tmp_path / 'health.json'
    monkeypatch.setattr(checks, 'WORKIQ_CACHE_FILE', str(path))
    winreg = MagicMock()
    winreg.OpenKey.side_effect = OSError('synthetic registry unavailable')
    with (patch.dict(sys.modules, {'winreg': winreg}),
          patch('platform.system', return_value='Windows'), patch('shutil.which', return_value='npx'),
          patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='SYNTHETIC PRIVATE IDENTITY', stderr=''))):
        result = checks.check_workiq()
    assert result.passed
    assert 'PRIVATE' not in path.read_text()
    assert 'user_name' not in path.read_text()


@pytest.mark.parametrize('entry', [None, 'invalid', {'mode': 'other', 'cached_at': datetime.now(timezone.utc).isoformat(), 'data': []},
                                   {'mode': 'email', 'cached_at': datetime.now(timezone.utc).isoformat(), 'data': ['bad']},
                                   {'mode': 'email', 'cached_at': datetime.now(timezone.utc).isoformat(), 'data': [], 'raw_narrative': 1}])
def test_malformed_cached_entries_never_become_records(entry):
    from connectors.workiq_bridge import _get_cached
    assert _get_cached('email', 'key', {'key': entry}, include_raw=True) == (None, None)


def test_health_receipt_links_and_oversized_files_refused(tmp_path):
    from lib.workiq_health import read_receipt, write_receipt
    target = tmp_path / 'target'
    target.write_bytes(b'x' * 3000)
    assert read_receipt(target, max_age=86400) is None
    path = tmp_path / 'health.json'
    path.symlink_to(target)
    assert read_receipt(path, max_age=86400) is None
    with pytest.raises(ValueError):
        write_receipt(path, available=True, auth_valid=True)
    assert target.read_bytes() == b'x' * 3000


def test_restored_preflight_never_probes_work_provider(tmp_path, monkeypatch):
    from preflight import integration_checks as checks
    (tmp_path / '.artha-restored.json').write_text('{}')
    monkeypatch.setattr(checks, 'ARTHA_DIR', str(tmp_path))
    monkeypatch.setattr(checks, 'WORKIQ_CACHE_FILE', str(tmp_path / 'health.json'))
    with patch('platform.system', return_value='Windows'), patch('subprocess.run') as provider:
        result = checks.check_workiq()
    assert not result.passed
    provider.assert_not_called()


def test_health_writer_surfaces_atomic_write_failure(tmp_path):
    from lib.workiq_health import write_receipt
    from types import SimpleNamespace
    with patch('lib.state_writer.write_atomic', return_value=SimpleNamespace(success=False)):
        with pytest.raises(OSError, match='could not be persisted'):
            write_receipt(tmp_path / 'health.json', available=True, auth_valid=True)


def test_redaction_policy_change_cannot_reuse_cached_work_records():
    from connectors import workiq_bridge as bridge
    cache = {}
    keywords = []
    raw = '2026-10-24 | 09:00 | 10:00 | Project Blue | Alice | Office | no'
    with (patch.object(bridge, '_load_cache', side_effect=lambda: dict(cache)),
          patch.object(bridge, '_save_cache', side_effect=lambda entries: cache.update(entries)),
          patch.object(bridge, '_redact_keywords', side_effect=lambda: list(keywords)),
          patch.object(bridge, '_redact_replacement', return_value='WORK'),
          patch.object(bridge, '_ask_workiq', return_value=raw) as provider):
        first = list(bridge.fetch(mode='calendar', start_date='2026-10-24', end_date='2026-10-24', auth_context={}))
        keywords.append('Blue')
        second = list(bridge.fetch(mode='calendar', start_date='2026-10-24', end_date='2026-10-24', auth_context={}))
    assert first[0]['title'] != second[0]['title']
    assert 'Blue' not in second[0]['title']
    assert provider.call_count == 2


def test_connector_implementation_revision_invalidates_work_cache(monkeypatch):
    from connectors import workiq_bridge as bridge
    before = bridge._cache_key_for_mode('email', lookback='24')
    monkeypatch.setattr(bridge, '_CACHE_IMPLEMENTATION', 'new revision', raising=False)
    assert bridge._cache_key_for_mode('email', lookback='24') != before
