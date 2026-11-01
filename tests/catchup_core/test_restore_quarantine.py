"""A restored installation must refuse activation before touching live storage or credentials."""
import pytest

from catchup_core.contracts import CatchupRequest, ExecutionMode
from catchup_core.runtime import build_deps
from catchup_core.session_lock import SessionBusy


@pytest.fixture
def restored(tmp_path):
    root = tmp_path / 'restored'
    root.mkdir()
    (root / '.artha-restored.json').write_text('{}')
    repo = root / 'repo'
    repo.mkdir()
    return repo


def test_restored_runtime_is_blocked_before_configuration_or_key_access(restored):
    with pytest.raises(SessionBusy, match='restored installation'):
        build_deps(CatchupRequest('restore-test', 'owner', 'cli', mode=ExecutionMode.SHADOW),
                   repo_root=restored, environ={})
    assert not (restored / 'config').exists()


def test_restored_queue_cannot_redirect_to_live_database(restored, tmp_path, monkeypatch):
    from action_queue import ActionQueue
    live = tmp_path / 'untouched/actions.db'
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(live))
    with pytest.raises(SessionBusy, match='restored installation'):
        ActionQueue(restored)
    assert not live.exists()


def test_restored_policy_is_refused_before_reading_payloads(restored):
    from lib.action_policy import load_dispatch_policy
    with pytest.raises(SessionBusy, match='restored installation'):
        load_dispatch_policy(restored, 'calendar_create')
