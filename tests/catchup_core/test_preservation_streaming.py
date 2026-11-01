"""Full installation scope and approved bytes, using synthetic encrypted checkpoints only."""
from contextlib import closing
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct

import pytest

from catchup_core import checkpoint, policy
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ledger import PermissionDenied
from test_cutover_fence import synthetic_recovery_acceptance

CIPHER = EnvelopeCipher(StaticKeyProvider({1: b'p' * 32}))
SCOPE = {'schema': 'artha.installation-preservation.v1', 'repository_root': 'repo', 'runtime_root': 'local',
         'root_names': ['local', 'repo'], 'inventory': 'recursive-fail-closed'}


def make_checkpoint(tmp_path, *, scope=SCOPE, name='cp', profile='personal'):
    repo, local = tmp_path / f'{name}_repo', tmp_path / f'{name}_local'
    (repo / 'state').mkdir(parents=True)
    (repo / 'config').mkdir()
    (repo / 'state/open_items.md').write_text('synthetic owner state')
    (repo / 'config/catchup_core.yaml').write_text('cutover: false\n')
    local.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        local.chmod(0o700)
    (local / 'catchup_core/shadow').mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        (local / 'catchup_core/shadow').chmod(0o700)
    with closing(sqlite3.connect(local / 'catchup_core/shadow/ledger.db')) as db, db:
        db.execute('CREATE TABLE records(value)')
        db.execute('INSERT INTO records VALUES(1)')
    return checkpoint.create(profile, {'repo': repo, 'local': local}, tmp_path / f'{name}_out', CIPHER,
        {'age-key/artha': b'AGE-SECRET-KEY-SYNTHETIC', 'artha-ledger/key.1': b'p' * 32,
         'artha-ledger-ids/key.1': b'i' * 32}, 'synthetic-passphrase-1234',
        now=datetime(2026, 10, 24, tzinfo=UTC), lock_path=tmp_path / f'{name}.lock',
        context={} if scope is None else {'preservation': scope})


def approved(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.mark.parametrize('missing', ['all', 'mac_personal', 'windows_work', 'personal_cloud_copy', 'privacy'])
def test_generic_recovery_receipt_without_required_native_observations_is_refused(tmp_path, missing):
    cp = make_checkpoint(tmp_path)
    second = tmp_path / 'independent.ack'
    second.write_bytes(cp.read_bytes())
    digest = approved(cp)['sha256']
    receipt = {'passed': True, 'owner_signed_at': '2026-10-24T12:00:00Z',
               'reconciled': True, 'checkpoint_sha256': digest}
    if missing != 'all':
        receipt['observations'] = {name: {'passed': True} for name in
                                   ('mac_personal', 'windows_work', 'personal_cloud_copy', 'privacy')
                                   if name != missing}
    rehearsal = tmp_path / 'synthetic-rehearsal.json'
    rehearsal.write_text(json.dumps(receipt))
    release = {'rollback_snapshot': approved(cp),
               'independent_copy': {**approved(second), 'failure_domain': 'cloud',
                                    'owner_verified_at': '2026-10-24T12:00:00Z', 'recovered_sha256': digest},
               'recovery_rehearsal': approved(rehearsal)}
    with pytest.raises(PermissionDenied, match='recovery'):
        policy._validate_preservation_evidence(release)


def test_generic_ack2_does_not_claim_full_installation_preservation(tmp_path):
    cp = make_checkpoint(tmp_path, scope=None)
    with pytest.raises(PermissionDenied, match='rollback snapshot'):
        policy.verify_preservation_checkpoint(cp.read_bytes(), CIPHER)


@pytest.mark.parametrize('scope', [
    {**SCOPE, 'schema': 'unknown'}, {**SCOPE, 'root_names': ['repo']},
    {**SCOPE, 'repository_root': 'state'}, {**SCOPE, 'runtime_root': 'shadow'},
    {**SCOPE, 'inventory': True}, {**SCOPE, 'root_names': ['repo', 'local', 'local']},
])
def test_scope_descriptor_cannot_hide_selective_or_ambiguous_coverage(tmp_path, scope):
    cp = make_checkpoint(tmp_path, scope=scope)
    with pytest.raises(PermissionDenied, match='rollback snapshot'):
        policy.verify_preservation_checkpoint(cp.read_bytes(), CIPHER)


def test_streamed_path_accepts_canonical_shadow_scope_without_read_bytes(tmp_path, monkeypatch):
    cp = make_checkpoint(tmp_path)
    digest = approved(cp)['sha256']
    original = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda path: pytest.fail('whole checkpoint read') if path == cp else original(path))
    manifest = policy.verify_preservation_checkpoint(cp, CIPHER, expected_sha256=digest)
    assert manifest['context']['preservation'] == SCOPE
    policy.verify_rollback({'release': {'rollback_snapshot': {'path': str(cp), 'sha256': digest}}}, CIPHER)


def test_cryptographically_valid_but_unapproved_checkpoint_is_refused(tmp_path):
    cp = make_checkpoint(tmp_path)
    with pytest.raises(PermissionDenied, match='approved'):
        policy.verify_preservation_checkpoint(cp, CIPHER, expected_sha256='0' * 64)


def test_unavailable_checkpoint_is_a_policy_refusal(tmp_path):
    with pytest.raises(PermissionDenied, match='rollback snapshot'):
        policy.verify_preservation_checkpoint(tmp_path / 'withdrawn.ack', CIPHER)


@pytest.mark.parametrize('path', ['', 'bad\x00path'])
def test_invalid_approved_path_is_a_policy_refusal(path):
    with pytest.raises(PermissionDenied):
        policy.verify_rollback({'release': {'rollback_snapshot': {'path': path, 'sha256': '0' * 64}}}, CIPHER)


@pytest.mark.parametrize('damage', ['physical', 'foreign-key'])
def test_every_embedded_sqlite_baseline_requires_physical_integrity(tmp_path, damage):
    cp = make_checkpoint(tmp_path)
    members = {}
    def collect(header, body):
        members.setdefault(header['path'], bytearray()).extend(body)
    manifest = checkpoint._scan(cp, CIPHER, collect)
    if damage == 'foreign-key':
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('CREATE TABLE parent(id PRIMARY KEY); '
                             'CREATE TABLE child(parent_id REFERENCES parent(id)); INSERT INTO child VALUES(7);')
            broken = db.serialize()
    else:
        broken = b'SQLite format 3\x00' + b'\xff' * 4080
    name = 'local/catchup_core/extra.db'
    manifest['entries'].append({'path': name, 'kind': 'sqlite', 'mode': 0o600, 'size': len(broken),
        'sha256': hashlib.sha256(broken).hexdigest(), 'source_sha256': hashlib.sha256(broken).hexdigest()})
    members[name] = broken
    with cp.open('rb') as handle:
        generation, escrow = checkpoint._read_head(handle)
    crafted = tmp_path / 'valid-authentication-invalid-database.ack'
    with crafted.open('wb') as handle:
        encoded = generation.encode()
        handle.write(b'ACK2' + struct.pack('>H', len(encoded)) + encoded + struct.pack('>I', len(escrow)) + escrow)
        writer = checkpoint._Writer(handle, CIPHER, generation)
        for entry in manifest['entries']:
            writer.frame('h', json.dumps({k: entry[k] for k in ('path', 'kind', 'mode')}).encode())
            writer.frame('d', bytes(members[entry['path']]))
            writer.frame('e', json.dumps({k: entry[k] for k in ('size', 'sha256')}).encode())
        writer.frame('m', json.dumps(manifest).encode())
        writer.frame('z', json.dumps({'members': len(manifest['entries'])}).encode())
    checkpoint._scan(crafted, CIPHER)  # Cryptographic and manifest coherence alone are insufficient.
    with pytest.raises(PermissionDenied, match='rollback snapshot'):
        policy.verify_preservation_checkpoint(crafted, CIPHER)


def test_sqlite_verification_enforces_its_memory_bound_by_spilling(tmp_path, monkeypatch):
    cp = make_checkpoint(tmp_path)
    monkeypatch.setattr(policy, '_VERIFY_IN_MEMORY', 1)
    assert policy.verify_preservation_checkpoint(cp, CIPHER)['profile'] == 'personal'


def test_distinct_same_disk_copy_needs_explicit_cloud_recovery_evidence(tmp_path):
    cp = make_checkpoint(tmp_path)
    second = tmp_path / 'copy.ack'
    second.write_bytes(cp.read_bytes())
    rehearsal = tmp_path / 'rehearsal.json'
    rehearsal.write_text(json.dumps(synthetic_recovery_acceptance(tmp_path, cp, CIPHER)))
    release = {'rollback_snapshot': approved(cp), 'independent_copy': approved(second),
               'recovery_rehearsal': approved(rehearsal)}
    with pytest.raises(PermissionDenied, match='independent'):
        policy._validate_preservation_evidence(release)
    release['independent_copy'].update(failure_domain='cloud', owner_verified_at='2026-10-24T12:00:00Z',
                                       recovered_sha256=approved(cp)['sha256'])
    policy._validate_preservation_evidence(release)
    release['independent_copy']['recovered_sha256'] = '0' * 64
    with pytest.raises(PermissionDenied, match='independent'):
        policy._validate_preservation_evidence(release)


def test_hardlinked_copy_is_not_independent_even_with_cloud_declaration(tmp_path):
    cp = make_checkpoint(tmp_path)
    second = tmp_path / 'hardlink.ack'
    second.hardlink_to(cp)
    rehearsal = tmp_path / 'rehearsal.json'
    rehearsal.write_text(json.dumps(synthetic_recovery_acceptance(tmp_path, cp, CIPHER)))
    record = {**approved(second), 'failure_domain': 'cloud', 'owner_verified_at': '2026-10-24T12:00:00Z',
              'recovered_sha256': approved(cp)['sha256']}
    with pytest.raises(PermissionDenied, match='independent'):
        policy._validate_preservation_evidence({'rollback_snapshot': approved(cp), 'independent_copy': record,
                                                'recovery_rehearsal': approved(rehearsal)})


def _spill_files(tmp_path):
    return sorted(Path(tmp_path).glob('artha-verify-*'))


def test_large_database_verification_spills_to_a_private_file_and_removes_it(tmp_path, monkeypatch):
    """Given a database above the memory threshold, when verified, then memory stays bounded and no spill remains."""
    import tracemalloc
    monkeypatch.setattr(policy, '_VERIFY_IN_MEMORY', 64 << 10)
    monkeypatch.setattr(checkpoint, '_CHUNK', 1 << 20)
    repo, local = tmp_path / 'big_repo', tmp_path / 'big_local'
    (repo / 'state').mkdir(parents=True)
    (repo / 'config').mkdir()
    (repo / 'state/open_items.md').write_text('synthetic owner state')
    (repo / 'config/catchup_core.yaml').write_text('cutover: false\n')
    (local / 'catchup_core/shadow').mkdir(mode=0o700, parents=True)
    with closing(sqlite3.connect(local / 'catchup_core/shadow/ledger.db')) as db, db:
        db.execute('CREATE TABLE records(value BLOB)')
        for _ in range(24):
            db.execute('INSERT INTO records VALUES(?)', (os.urandom(1 << 20),))
    cp = checkpoint.create('personal', {'repo': repo, 'local': local}, tmp_path / 'big_out', CIPHER,
        {'age-key/artha': b'AGE-SECRET-KEY-SYNTHETIC', 'artha-ledger/key.1': b'p' * 32,
         'artha-ledger-ids/key.1': b'i' * 32}, 'synthetic-passphrase-1234',
        now=datetime(2026, 10, 24, tzinfo=UTC), lock_path=tmp_path / 'big.lock',
        context={'preservation': SCOPE})
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    tracemalloc.start()
    try:
        manifest = policy.verify_preservation_checkpoint(cp, CIPHER)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert manifest['profile'] == 'personal'
    assert peak < 8 << 20  # the database is 24 MiB
    assert _spill_files(tmp_path) == []


def test_damaged_large_database_is_refused_and_its_spill_is_removed(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr(policy, '_VERIFY_IN_MEMORY', 1)
    def damaged(db):
        raise ValueError('checkpoint database integrity failed')
    monkeypatch.setattr(policy._DatabaseCheck, '_verify', staticmethod(damaged))
    cp = make_checkpoint(tmp_path)
    with pytest.raises(PermissionDenied):
        policy.verify_preservation_checkpoint(cp, CIPHER)
    assert _spill_files(tmp_path) == []
