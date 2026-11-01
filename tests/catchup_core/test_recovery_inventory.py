"""Recovery applies the same reviewed dependency inventory as capture; synthetic checkpoints only."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from catchup_core import checkpoint as ck
from catchup_core import checkpoint_cli as cli
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider

PASS = 'correct horse battery staple'
KEY = b'k' * 32
ESCROW = {'age-key/artha': b'AGE-SECRET-KEY-SYNTHETIC', 'artha-ledger/key.1': KEY, 'artha-ledger-ids/key.1': b'i' * 32,
          'artha-ledger/current_version': b'1', 'artha-ledger-ids/current_version': b'1'}


def registry(*entries):
    return {'schema': 'artha.recovery-registry.v1', 'profile': 'personal', 'host': 'h', 'revision': 3,
            'legacy_review': None, 'entries': [
                {'kind': kind, 'service': service, 'account': account, 'generation': generation,
                 'state': 'active', 'attempt': 'a'} for kind, service, account, generation in entries]}


FULL = (('vault', 'age-key', 'artha', 'legacy'),
        ('key', 'artha-ledger', 'key.1', '1'), ('key_head', 'artha-ledger', 'current_version', '1'),
        ('key', 'artha-ledger-ids', 'key.1', '1'), ('key_head', 'artha-ledger-ids', 'current_version', '1'))


def checkpoint(tmp_path, context, escrow=ESCROW):
    root = tmp_path / 'src'
    root.mkdir(exist_ok=True)
    (root / 'a.txt').write_text('synthetic')
    cipher = EnvelopeCipher(StaticKeyProvider({1: KEY}))
    return ck.create('personal', {'repo': root}, tmp_path / 'out', cipher, escrow, PASS,
                     now=datetime(2026, 10, 24, tzinfo=UTC), lock_path=tmp_path / 'lock', context=context)


def test_reviewed_inventory_with_every_dependency_classified_authenticates(tmp_path):
    path = checkpoint(tmp_path, {'recovery_registry': registry(*FULL)})
    assert cli._inspect_recovery(path, PASS)['verified'] is True


def test_checkpoint_without_a_dependency_inventory_is_not_recoverable_evidence(tmp_path):
    """Given a checkpoint with no recorded registry, when recovery is inspected, then it is blocked."""
    path = checkpoint(tmp_path, {})
    with pytest.raises(ck.CheckpointBlocked, match='dependency inventory'):
        cli._inspect_recovery(path, PASS)


def test_escrow_item_missing_from_the_recorded_inventory_is_unclassified_and_blocks(tmp_path):
    """Given a recovery record the inventory never named, when recovery is inspected, then it is blocked."""
    escrow = {**ESCROW, 'artha-bundle-ghost/secret': b'g' * 32}
    path = checkpoint(tmp_path, {'recovery_registry': registry(*FULL)}, escrow)
    with pytest.raises(ck.CheckpointBlocked, match='absent from the reviewed registry'):
        cli._inspect_recovery(path, PASS)


def test_key_head_missing_from_the_recorded_inventory_blocks_recovery(tmp_path):
    """Given an inventory without a required key head, when recovery is inspected, then it is blocked."""
    entries = tuple(entry for entry in FULL if entry[:3] != ('key_head', 'artha-ledger', 'current_version'))
    escrow = {name: value for name, value in ESCROW.items() if name != 'artha-ledger/current_version'}
    path = checkpoint(tmp_path, {'recovery_registry': registry(*entries)}, escrow)
    with pytest.raises(ck.CheckpointBlocked, match='required key head'):
        cli._inspect_recovery(path, PASS)


def test_pending_dependency_in_the_recorded_inventory_blocks_recovery(tmp_path):
    document = registry(*FULL)
    document['entries'][1]['state'] = 'pending'
    path = checkpoint(tmp_path, {'recovery_registry': document})
    with pytest.raises(ck.CheckpointBlocked):
        cli._inspect_recovery(path, PASS)
