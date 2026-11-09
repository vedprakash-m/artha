"""Producer inputs must match the pinned Work journal before a callback sees them."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core import work_privacy
from lib import work_publication as wp
from test_work_publication import enroll, never, work  # noqa: F401


def test_producer_read_uses_the_bounded_work_reader(tmp_path, monkeypatch):
    path = tmp_path / 'member.md'
    path.write_bytes(b'synthetic')
    reads = []

    def bounded(target, bound):
        reads.append((target, bound))
        return b'synthetic'

    monkeypatch.setattr(work_privacy, '_read', bounded)
    monkeypatch.setattr(Path, 'read_bytes', lambda *_: pytest.fail('unbounded producer read'))
    assert wp._read(SimpleNamespace(batch=None), path) == b'synthetic'
    assert reads == [(path, work_privacy._MAX_MEMBER_BYTES)]


@pytest.mark.parametrize('mutation', ['replace', 'delete', 'create'])
def test_changed_member_never_reaches_the_producer_callback(work, monkeypatch, mutation):  # noqa: F811
    enroll(work)
    protocol = work['make']()
    previous = wp.set_protocol_factory(lambda: protocol)
    path = work['state'] / ('new.md' if mutation == 'create' else 'work-notes.md')
    transaction = protocol.transaction

    @contextmanager
    def change_after_pin():
        with transaction() as tx:
            if mutation == 'delete':
                path.unlink()
            else:
                path.write_bytes(b'changed after the journal check')
            yield tx

    monkeypatch.setattr(protocol, 'transaction', change_after_pin)
    try:
        with pytest.raises(wp.WorkPublicationBlocked, match='changed|stale'):
            wp.update_bytes(path, lambda _: pytest.fail('unverified content reached callback'), legacy=never)
        assert not (protocol.authority_dir / 'pending').exists()
    finally:
        wp.set_protocol_factory(previous)


def test_staged_reads_do_not_require_a_persisted_member(tmp_path):
    path = tmp_path / 'new.md'
    assert wp._read(SimpleNamespace(batch={path: b'staged'}), path) == b'staged'
    assert wp._read(SimpleNamespace(batch={path: None}), path) is None


def test_missing_producer_input_is_distinct_from_an_unreadable_member(tmp_path, monkeypatch):
    path = tmp_path / 'missing.md'
    assert wp._read(SimpleNamespace(batch=None), path) is None
    path.write_bytes(b'synthetic')
    monkeypatch.setattr(work_privacy, '_read', lambda *_: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(wp.WorkPublicationBlocked, match='read'):
        wp._read(SimpleNamespace(batch=None), path)
