"""Legacy Work recovery status preserves its current-authority refusal boundaries."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from catchup_core import checkpoint as ck
from catchup_core import checkpoint_cli as cli
from catchup_core.work_privacy import WorkPrivacyBlocked


@pytest.mark.parametrize('case', ['valid', 'unbound', 'unavailable', 'changed'])
def test_legacy_work_status_requires_a_matching_current_head(case):
    binding = {'members': {'work_state/note.txt': {}}}
    manifest = {'schema': 'artha.checkpoint.v2', 'context': {'work_binding': binding}}
    protocol = SimpleNamespace(check_checkpoint=lambda _: {'head': 'pinned'},
                               denied_members=lambda *_: {'work_state/note.txt'},
                               current=lambda: {'head': 'pinned'})
    if case == 'unbound':
        manifest['context'] = {}
    elif case == 'unavailable':
        def refuse(_):
            raise WorkPrivacyBlocked('synthetic authority refusal')
        protocol.check_checkpoint = refuse
    elif case == 'changed':
        protocol.current = lambda: {'head': 'changed'}
    if case == 'valid':
        assert cli._work_recovery_status(manifest, protocol, None) == {
            'work_authority_verified': True, 'work_members_blocked': 1}
    else:
        with pytest.raises(ck.CheckpointBlocked):
            cli._work_recovery_status(manifest, protocol, None)
