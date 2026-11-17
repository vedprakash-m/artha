"""Synthetic authenticated Work stream frames; no live Work data is read."""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core import checkpoint as ck
from catchup_core import checkpoint_cli as cli
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider

CIPHER = EnvelopeCipher(StaticKeyProvider({1: b's' * 32}))
GENERATION = 'ck_synthetic_streams'
ORDINARY = b'ordinary synthetic bytes'
OWNER = 'work_state/note.txt'
NOW = datetime(2026, 11, 6, tzinfo=UTC)


@pytest.mark.parametrize('modules', [
    ('catchup_core.checkpoint', 'catchup_core.recovery_quarantine'),
    ('catchup_core.recovery_quarantine', 'catchup_core.checkpoint'),
    ('catchup_core.work_privacy', 'catchup_core.checkpoint'),
    ('catchup_core.quarantine_review', 'catchup_core.checkpoint'),
])
def test_work_modules_import_in_either_order(modules):
    env = dict(os.environ)
    scripts = str(Path(__file__).resolve().parents[2] / 'scripts')
    env['PYTHONPATH'] = os.pathsep.join((scripts, env.get('PYTHONPATH', '')))
    subprocess.run(
        [sys.executable, '-c', 'import importlib, sys; [importlib.import_module(n) for n in sys.argv[1:]]',
         *modules],
        check=True, timeout=30, env=env,
    )


def _identity(owner, owner_kind, name):
    return {'kind': 'stream', 'owner': owner, 'owner_kind': owner_kind,
            'name': name, 'stream_type': '$DATA'}


def _ending(data):
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def _key(identity):
    return json.dumps([identity['owner'], identity['owner_kind'], identity['name'], identity['stream_type']],
                      separators=(',', ':'), ensure_ascii=True)


def _checkpoint(path, *, streams=None, magic=b'ACK3', schema='artha.checkpoint.v3', profile='work',
                member_path=OWNER,
                edit_manifest=lambda _manifest: None, edit_terminator=lambda _terminator: None):
    streams = streams if streams is not None else [
        (_identity(member_path, 'file', 'synthetic secret name'), b'synthetic secret stream bytes'),
        (_identity(member_path, 'file', 'empty'), b''),
        (_identity('work_state/folder', 'directory', 'directory stream'), b'folder bytes'),
        (_identity('work_state', 'directory', 'root stream'), b'root bytes'),
    ]
    member = {'path': member_path, 'kind': 'file', 'mode': 0o600}
    member_entry = {**member, **_ending(ORDINARY), 'source_sha256': _ending(ORDINARY)['sha256']}
    stream_entries = [{**identity, **_ending(body)} for identity, body in streams]
    binding = {'schema': 'artha.work-checkpoint-binding.v2', 'profile': 'work', 'plaintext_allowed': False,
               'identity': 'a' * 64, 'generation': 1, 'head_sha256': 'b' * 64, 'mac': 'c' * 64,
               'members': {member_path: _ending(ORDINARY)},
               'streams': {_key(identity): _ending(body) for identity, body in streams}}
    manifest = {'schema': schema, 'generation': GENERATION, 'profile': profile,
                'escrow_sha256': hashlib.sha256(b'').hexdigest(), 'entries': [member_entry],
                'streams': stream_entries, 'roots': ['work_state'], 'excluded': {},
                'context': {'work_binding': binding}}
    terminator = {'members': 1, 'streams': len(stream_entries)}
    edit_manifest(manifest)
    edit_terminator(terminator)
    with path.open('wb') as handle:
        generation = GENERATION.encode()
        handle.write(magic + struct.pack('>H', len(generation)) + generation + struct.pack('>I', 0))
        writer = ck._Writer(handle, CIPHER, GENERATION)
        for header, body in [(member, ORDINARY), *streams]:
            writer.frame('h', json.dumps(header).encode())
            if body:
                writer.frame('d', body)
            writer.frame('e', json.dumps(_ending(body)).encode())
        writer.frame('m', json.dumps(manifest).encode())
        writer.frame('z', json.dumps(terminator).encode())
    return path, manifest


def _rewrite_frames(path, mutate):
    with path.open('rb') as handle:
        generation, escrow = ck._read_head(handle)
        frames = list(ck._frames(handle, CIPHER, generation))
    mutate(frames)
    with path.open('wb') as handle:
        encoded = generation.encode()
        handle.write(b'ACK3' + struct.pack('>H', len(encoded)) + encoded
                     + struct.pack('>I', len(escrow)) + escrow)
        writer = ck._Writer(handle, CIPHER, generation)
        for kind, data in frames:
            writer.frame(kind, data)


def test_work_v3_authenticates_exact_typed_stream_frames_and_contents(tmp_path):
    path, expected = _checkpoint(tmp_path / 'work.ack')
    seen = []
    actual = ck._scan(path, CIPHER, lambda header, chunk: seen.append((header, chunk)))

    assert actual == expected
    assert [(entry['owner'], entry['owner_kind'], entry['name'], entry['size'])
            for entry in actual['streams']] == [
        (OWNER, 'file', 'synthetic secret name', 29),
        (OWNER, 'file', 'empty', 0),
        ('work_state/folder', 'directory', 'directory stream', 12),
        ('work_state', 'directory', 'root stream', 10),
    ]
    assert [(header['name'], data) for header, data in seen if header['kind'] == 'stream'] == [
        ('synthetic secret name', b'synthetic secret stream bytes'),
        ('directory stream', b'folder bytes'),
        ('root stream', b'root bytes'),
    ]
    raw = path.read_bytes()
    assert raw.startswith(b'ACK3') and not raw.startswith(b'ACK2')
    assert b'synthetic secret name' not in raw and b'synthetic secret stream bytes' not in raw


def _omission_context(manifest):
    from catchup_core.work_privacy import STREAM_POLICY
    manifest['context']['work_binding'].update(schema='artha.work-checkpoint-binding.v3',
                                               stream_policy=STREAM_POLICY)
    manifest['context']['work_stream_omissions'] = {
        'policy': STREAM_POLICY, 'semantics': 'observed_names_not_preserved', 'roots': sorted(manifest['roots']),
        'streams': [{**_identity(OWNER, 'file', 'sec.endpointdlp'), 'size': 17}]}


def test_endpointdlp_manifest_authenticates_explicit_omission_report(tmp_path):
    path, expected = _checkpoint(tmp_path / 'work.ack', edit_manifest=_omission_context)
    assert ck.verify(path, CIPHER) == expected


@pytest.mark.parametrize('fault', ['missing', 'wrong-policy', 'wrong-name', 'wrong-owner', 'bool-size', 'duplicate'])
def test_endpointdlp_manifest_rejects_incomplete_or_ambiguous_omissions(tmp_path, fault):
    def damage(manifest):
        _omission_context(manifest)
        report = manifest['context']['work_stream_omissions']
        if fault == 'missing':
            del manifest['context']['work_stream_omissions']
        elif fault == 'wrong-policy':
            report['policy'] = 'unknown'
        elif fault == 'duplicate':
            report['streams'].append(dict(report['streams'][0]))
        else:
            report['streams'][0][{'wrong-name': 'name', 'wrong-owner': 'owner', 'bool-size': 'size'}[fault]] = {
                'wrong-name': 'sec.endpointdlp.extra', 'wrong-owner': 'other/file', 'bool-size': True}[fault]
    path, _ = _checkpoint(tmp_path / 'bad.ack', edit_manifest=damage)
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS omission capture')
@pytest.mark.parametrize('change', ['mandatory', 'same-size-primary', 'replaced-owner', 'unknown-stream'])
def test_endpointdlp_capture_recheck_allows_only_exempt_metadata_change(tmp_path, change):
    from catchup_core.work_privacy import STREAM_POLICY
    root = tmp_path / 'work'
    root.mkdir()
    owner = root / 'note.txt'
    owner.write_bytes(ORDINARY)
    Path(str(owner) + ':sec.endpointdlp').write_bytes(b'old volatile metadata')
    Path(str(owner) + ':mandatory').write_bytes(b'mandatory bytes')
    roots = {'work_state': root}
    captured = ck._capture_work_streams(roots, {}, stream_policy=STREAM_POLICY)
    Path(str(owner) + ':sec.endpointdlp').write_bytes(b'changed volatile metadata')
    ck._recheck_work_streams(captured, roots, {})
    assert [(header['name'], data) for header, data in captured.streams] == [('mandatory', b'mandatory bytes')]
    assert captured.omissions[0]['name'] == 'sec.endpointdlp'
    if change == 'mandatory':
        Path(str(owner) + ':mandatory').write_bytes(b'changed mandatory bytes')
    elif change == 'same-size-primary':
        owner.write_bytes(b'x' * len(ORDINARY))
    elif change == 'replaced-owner':
        owner.unlink()
        owner.write_bytes(ORDINARY)
    else:
        Path(str(owner) + ':unknown').write_bytes(b'new stream')
    with pytest.raises(ck.CheckpointBlocked):
        ck._recheck_work_streams(captured, roots, {})


@pytest.mark.parametrize(('magic', 'schema', 'profile'), [
    (b'ACK2', 'artha.checkpoint.v3', 'work'),
    (b'ACK3', 'artha.checkpoint.v2', 'work'),
    (b'ACK3', 'artha.checkpoint.v3', 'personal'),
])
def test_new_schema_cannot_be_confused_with_legacy_or_personal(tmp_path, magic, schema, profile):
    path, _ = _checkpoint(tmp_path / 'bad.ack', magic=magic, schema=schema, profile=profile)
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


@pytest.mark.parametrize('defect', ['missing', 'wrong-size', 'wrong-hash', 'extra', 'wrong-binding'])
def test_manifest_and_binding_must_describe_all_captured_streams(tmp_path, defect):
    def damage(manifest):
        if defect == 'missing':
            manifest['streams'].pop()
        elif defect == 'wrong-size':
            manifest['streams'][0]['size'] += 1
        elif defect == 'wrong-hash':
            manifest['streams'][0]['sha256'] = '0' * 64
        elif defect == 'extra':
            manifest['streams'].append(dict(manifest['streams'][0]))
        else:
            manifest['context']['work_binding']['streams'].clear()
    path, _ = _checkpoint(tmp_path / 'bad.ack', edit_manifest=damage)
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


@pytest.mark.parametrize('identity', [
    _identity(OWNER, 'file', 'bad:name'),
    _identity(OWNER, 'file', 'bad/name'),
    _identity(OWNER, 'file', 'bad\\name'),
    _identity(OWNER, 'file', 'trailing.'),
    _identity(OWNER, 'file', 'invalid\ud800'),
    _identity('work_state/missing.txt', 'file', 'metadata'),
    _identity(OWNER, 'directory', 'metadata'),
    _identity('work_state/../outside', 'directory', 'metadata'),
    _identity('other_root', 'directory', 'metadata'),
    {**_identity(OWNER, 'file', 'metadata'), 'stream_type': '$OTHER'},
])
def test_invalid_stream_identity_is_refused_without_disclosing_name(tmp_path, identity):
    path, _ = _checkpoint(tmp_path / 'bad.ack', streams=[(identity, b'data')])
    with pytest.raises(ck.CheckpointError) as caught:
        ck.verify(path, CIPHER)
    assert identity['name'] not in str(caught.value)


@pytest.mark.parametrize('names', [('Name', 'name'), ('caf\u00e9', 'cafe\u0301')])
def test_ambiguous_names_on_one_owner_are_refused(tmp_path, names):
    streams = [(_identity(OWNER, 'file', name), b'data') for name in names]
    path, _ = _checkpoint(tmp_path / 'bad.ack', streams=streams)
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


def test_terminator_stream_count_is_authenticated_and_checked(tmp_path):
    path, _ = _checkpoint(tmp_path / 'bad.ack',
                          edit_terminator=lambda terminator: terminator.update(streams=0))
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


def test_stream_binding_rejects_boolean_size_even_when_it_compares_equal(tmp_path):
    streams = [(_identity(OWNER, 'file', 'metadata'), b'x')]

    def damage(manifest):
        manifest['context']['work_binding']['streams'][_key(streams[0][0])]['size'] = True

    path, _ = _checkpoint(tmp_path / 'bad.ack', streams=streams, edit_manifest=damage)
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


def test_non_string_stream_owner_kind_is_refused_as_malformed_metadata(tmp_path):
    path, _ = _checkpoint(tmp_path / 'bad.ack', streams=[(_identity(OWNER, [], 'metadata'), b'data')])
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


@pytest.mark.parametrize('surrogate', [chr(0xD800), chr(0xDFFF)])
def test_v3_member_paths_must_be_valid_unicode(tmp_path, surrogate):
    path, _ = _checkpoint(tmp_path / 'bad.ack', streams=[],
                          member_path=f'work_state/synthetic-{surrogate}.txt')
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


@pytest.mark.parametrize('frame_kind', ['h', 'e', 'm', 'z'])
def test_duplicate_json_fields_are_refused_in_every_v3_metadata_frame(tmp_path, frame_kind):
    path, _ = _checkpoint(tmp_path / 'bad.ack', streams=[])

    def damage(frames):
        index = next(index for index, (kind, _) in enumerate(frames) if kind == frame_kind)
        kind, data = frames[index]
        field = {'h': 'kind', 'e': 'size', 'm': 'schema', 'z': 'members'}[kind]
        encoded = json.dumps({field: json.loads(data)[field]}).encode()[1:-1]
        frames[index] = kind, data[:-1] + b', ' + encoded + b'}'

    _rewrite_frames(path, damage)
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


@pytest.mark.parametrize('defect', ['duplicate-header', 'missing-data', 'manifest-before-end',
                                   'data-after-manifest', 'duplicate-terminator'])
def test_authenticated_frames_still_require_the_exact_order(tmp_path, defect):
    path, _ = _checkpoint(tmp_path / 'bad.ack', streams=[])

    def damage(frames):
        if defect == 'duplicate-header':
            frames.insert(1, frames[0])
        elif defect == 'missing-data':
            frames.pop(1)
        elif defect == 'manifest-before-end':
            frames[2], frames[3] = frames[3], frames[2]
        elif defect == 'data-after-manifest':
            frames.insert(-1, ('d', b'late synthetic content'))
        else:
            frames.append(frames[-1])

    _rewrite_frames(path, damage)
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


@pytest.mark.parametrize(('limit', 'value'), [
    ('_MAX_WORK_STREAMS', 1),
    ('_MAX_WORK_STREAMS_PER_OWNER', 1),
    ('_MAX_WORK_STREAM_BYTES', 1),
    ('_MAX_WORK_STREAM_TOTAL_BYTES', 1),
])
def test_stream_limits_are_enforced_before_accepting_the_manifest(tmp_path, monkeypatch, limit, value):
    streams = [(_identity(OWNER, 'file', 'first'), b'ab'),
               (_identity(OWNER, 'file', 'second'), b'cd')]
    path, _ = _checkpoint(tmp_path / 'bad.ack', streams=streams)
    monkeypatch.setattr(ck, limit, value)
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


def test_legacy_magic_reader_refuses_ack3_before_reading_members(tmp_path, monkeypatch):
    path, _ = _checkpoint(tmp_path / 'work.ack')
    monkeypatch.setattr(ck, '_WORK_STREAM_MAGIC', ck._MAGIC)
    with pytest.raises(ck.CheckpointError, match='not a checkpoint'):
        ck.verify(path, CIPHER)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream capture')
def test_capture_enumerates_included_file_directory_and_root_streams(tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    document = root / 'note.txt'
    document.write_bytes(ORDINARY)
    directory = root / 'folder'
    directory.mkdir()
    excluded = root / 'excluded'
    excluded.mkdir()
    (excluded / 'hidden.txt').write_bytes(b'excluded synthetic file')
    Path(str(document) + ':one').write_bytes(b'file bytes')
    Path(str(document) + ':empty').write_bytes(b'')
    Path(str(directory) + ':two').write_bytes(b'directory bytes')
    Path(str(root) + ':root').write_bytes(b'root bytes')
    Path(str(excluded) + ':not-captured').write_bytes(b'excluded stream')

    snapshot = ck._capture_work_streams({'work_state': root},
                                        {'work_state/excluded': 'excluded synthetic subtree'})

    assert set(snapshot.files) == {OWNER}
    assert set(snapshot.directories) == {'work_state', 'work_state/folder'}
    assert set(snapshot.owner_snapshots) == {'work_state', 'work_state/folder', OWNER}
    assert {(_key(header), data) for header, data in snapshot.streams} == {
        (_key(_identity(OWNER, 'file', 'one')), b'file bytes'),
        (_key(_identity(OWNER, 'file', 'empty')), b''),
        (_key(_identity('work_state/folder', 'directory', 'two')), b'directory bytes'),
        (_key(_identity('work_state', 'directory', 'root')), b'root bytes'),
    }


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream capture')
@pytest.mark.parametrize('mutation', ['add-stream', 'remove-stream', 'change-stream',
                                     'replace-owner', 'add-empty-directory', 'add-file'])
def test_capture_recheck_refuses_any_included_owner_or_stream_change(tmp_path, mutation):
    root = tmp_path / 'work'
    root.mkdir()
    document = root / 'note.txt'
    document.write_bytes(ORDINARY)
    stream = Path(str(document) + ':synthetic')
    stream.write_bytes(b'original')
    roots = {'work_state': root}
    snapshot = ck._capture_work_streams(roots, {})

    if mutation == 'add-stream':
        Path(str(document) + ':new').write_bytes(b'new')
    elif mutation == 'remove-stream':
        stream.unlink()
    elif mutation == 'change-stream':
        stream.write_bytes(b'changed')
    elif mutation == 'replace-owner':
        document.unlink()
        document.write_bytes(ORDINARY)
        stream.write_bytes(b'original')
    elif mutation == 'add-empty-directory':
        (root / 'empty').mkdir()
    else:
        (root / 'new.txt').write_bytes(b'new')

    with pytest.raises(ck.CheckpointBlocked) as caught:
        ck._recheck_work_streams(snapshot, roots, {})
    assert 'synthetic' not in str(caught.value)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream capture')
def test_capture_includes_single_file_roots_without_inventing_a_directory_owner(tmp_path):
    artifact = tmp_path / 'dependency.txt'
    artifact.write_bytes(b'artifact')
    Path(str(artifact) + ':metadata').write_bytes(b'dependency stream')

    snapshot = ck._capture_work_streams({'work_artifact_1': artifact}, {})

    assert set(snapshot.files) == {'work_artifact_1/dependency.txt'}
    assert not snapshot.directories
    assert {_key(header) for header, _ in snapshot.streams} == {
        _key(_identity('work_artifact_1/dependency.txt', 'file', 'metadata'))}


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream capture')
def test_capture_refuses_total_stream_bytes_over_its_bound(tmp_path, monkeypatch):
    root = tmp_path / 'work'
    root.mkdir()
    document = root / 'note.txt'
    document.write_bytes(ORDINARY)
    Path(str(document) + ':synthetic').write_bytes(b'too many bytes')
    monkeypatch.setattr(ck, '_MAX_WORK_STREAM_TOTAL_BYTES', 2)

    with pytest.raises(ck.CheckpointBlocked, match='bound'):
        ck._capture_work_streams({'work_state': root}, {})


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream capture')
def test_capture_does_not_disclose_stream_inventory_errors(tmp_path, monkeypatch):
    from catchup_core import quarantine_fs_windows as fs

    root = tmp_path / 'work'
    root.mkdir()

    def unavailable(*_args, **_kwargs):
        raise fs.QuarantineFilesystemError('synthetic secret stream name')

    monkeypatch.setattr(fs, 'read_named_streams_with_snapshot', unavailable)
    with pytest.raises(ck.CheckpointBlocked) as caught:
        ck._capture_work_streams({'work_state': root}, {})
    assert 'synthetic secret' not in str(caught.value)


@pytest.mark.parametrize('location', ['root', 'child'])
def test_work_inventory_refuses_reparse_directories_before_traversal(tmp_path, monkeypatch, location):
    root = tmp_path / 'work'
    child = root / 'untrusted'
    child.mkdir(parents=True)
    (child / 'synthetic.txt').write_bytes(b'synthetic')
    flagged = root if location == 'root' else child
    lstat = Path.lstat
    scandir = ck.os.scandir
    visited = []

    def flagged_lstat(path, *args, **kwargs):
        info = lstat(path, *args, **kwargs)
        if path == flagged:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    def track_scandir(path):
        visited.append(Path(path))
        return scandir(path)

    monkeypatch.setattr(Path, 'lstat', flagged_lstat)
    monkeypatch.setattr(ck.os, 'scandir', track_scandir)
    directories = {}
    found, problems, _ = ck._walk({'work_state': root}, {}, stream_directories=directories)
    assert problems
    assert flagged not in visited
    assert 'work_state/untrusted/synthetic.txt' not in found


@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows directory junction')
@pytest.mark.parametrize('location', ['root', 'child'])
def test_work_inventory_refuses_native_junction_before_traversal(tmp_path, monkeypatch, location):
    root = tmp_path / 'work'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'synthetic.txt').write_bytes(b'synthetic')
    junction = tmp_path / 'root-link' if location == 'root' else root / 'child-link'
    subprocess.run([os.environ['ComSpec'], '/c', 'mklink', '/J', str(junction), str(outside)],
                   check=True, capture_output=True)
    inventory_root = junction if location == 'root' else root
    scandir = ck.os.scandir
    visited = []

    def track_scandir(path):
        visited.append(Path(path))
        return scandir(path)

    monkeypatch.setattr(ck.os, 'scandir', track_scandir)
    found, problems, _ = ck._walk({'work_state': inventory_root}, {}, stream_directories={})
    assert problems
    assert junction not in visited
    assert not found


class _FrameRecorder:
    def __init__(self):
        self.frames = []

    def frame(self, kind, data):
        self.frames.append((kind, data))


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS Work member capture')
def test_native_work_member_capture_matches_pinned_identity_and_binding(tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    document = root / 'note.txt'
    body = b's' * (ck._CHUNK + 17)
    document.write_bytes(body)
    captured = ck._capture_work_streams({'work_state': root}, {})
    writer = _FrameRecorder()
    digest = hashlib.sha256(body).hexdigest()

    entry = ck._stream_work_member(writer, OWNER, document, captured.owner_snapshots[OWNER], digest, 0o600)

    assert entry.path == OWNER and entry.size == len(body) and entry.sha256 == digest
    assert [kind for kind, _ in writer.frames] == ['h', 'd', 'd', 'e']
    assert b''.join(data for kind, data in writer.frames if kind == 'd') == body
    assert all(len(data) <= ck._CHUNK for kind, data in writer.frames if kind == 'd')


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS Work member capture')
@pytest.mark.parametrize('change', ['replaced', 'wrong-digest', 'sqlite'])
def test_native_work_member_capture_refuses_changed_or_unsupported_member(tmp_path, change):
    root = tmp_path / 'work'
    root.mkdir()
    document = root / 'note.txt'
    body = b'SQLite format 3\x00synthetic' if change == 'sqlite' else b'synthetic member'
    document.write_bytes(body)
    captured = ck._capture_work_streams({'work_state': root}, {})
    if change == 'replaced':
        document.unlink()
        document.write_bytes(body)
    digest = '0' * 64 if change == 'wrong-digest' else hashlib.sha256(body).hexdigest()
    writer = _FrameRecorder()

    with pytest.raises(ck.CheckpointBlocked):
        ck._stream_work_member(writer, OWNER, document, captured.owner_snapshots[OWNER], digest, 0o600)
    assert not any(kind == 'e' for kind, _ in writer.frames)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS Work member capture')
def test_native_work_member_capture_refuses_mutation_during_read(tmp_path, monkeypatch):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineFile

    root = tmp_path / 'work'
    root.mkdir()
    document = root / 'note.txt'
    body = b'a' * 131072
    document.write_bytes(body)
    captured = ck._capture_work_streams({'work_state': root}, {})
    read_chunk = WindowsQuarantineFile.read_chunk
    changed = False

    def mutate_during_read(handle, maximum):
        nonlocal changed
        data = read_chunk(handle, maximum)
        if data and not changed:
            changed = True
            document.write_bytes(b'b' * len(body))
        return data

    monkeypatch.setattr(WindowsQuarantineFile, 'read_chunk', mutate_during_read)
    writer = _FrameRecorder()
    with pytest.raises(ck.CheckpointBlocked):
        ck._stream_work_member(writer, OWNER, document, captured.owner_snapshots[OWNER],
                               hashlib.sha256(body).hexdigest(), 0o600)
    assert changed
    assert not any(kind == 'e' for kind, _ in writer.frames)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS Work member capture')
@pytest.mark.parametrize('body', [b'', b'short synthetic bytes', b'SQLite format 3\x00synthetic'])
def test_native_work_member_capture_handles_short_native_reads(tmp_path, monkeypatch, body):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineFile

    root = tmp_path / 'work'
    root.mkdir()
    document = root / 'note.txt'
    document.write_bytes(body)
    captured = ck._capture_work_streams({'work_state': root}, {})
    read_chunk = WindowsQuarantineFile.read_chunk
    monkeypatch.setattr(WindowsQuarantineFile, 'read_chunk',
                        lambda handle, maximum: read_chunk(handle, min(maximum, 3)))
    writer = _FrameRecorder()
    args = (writer, OWNER, document, captured.owner_snapshots[OWNER], hashlib.sha256(body).hexdigest(), 0o600)

    if body.startswith(b'SQLite format 3\x00'):
        with pytest.raises(ck.CheckpointBlocked):
            ck._stream_work_member(*args)
        assert not writer.frames
    else:
        entry = ck._stream_work_member(*args)
        assert entry.size == len(body)
        assert b''.join(data for kind, data in writer.frames if kind == 'd') == body
        assert sum(kind == 'd' for kind, _ in writer.frames) == bool(body)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS Work member capture')
def test_native_work_member_capture_refuses_oversized_member_before_read(tmp_path, monkeypatch):
    from catchup_core import recovery_quarantine
    from catchup_core.quarantine_fs_windows import WindowsQuarantineFile

    root = tmp_path / 'work'
    root.mkdir()
    document = root / 'note.txt'
    body = b'oversized synthetic member'
    document.write_bytes(body)
    captured = ck._capture_work_streams({'work_state': root}, {})
    monkeypatch.setattr(recovery_quarantine, 'MAX_MEMBER_BYTES', len(body) - 1)

    def unexpected_read(*_args):
        pytest.fail('oversized member was read')

    monkeypatch.setattr(WindowsQuarantineFile, 'read_chunk', unexpected_read)
    writer = _FrameRecorder()
    with pytest.raises(ck.CheckpointBlocked):
        ck._stream_work_member(writer, OWNER, document, captured.owner_snapshots[OWNER],
                               hashlib.sha256(body).hexdigest(), 0o600)
    assert not writer.frames


def test_versioned_work_restore_requires_encrypted_quarantine(tmp_path):
    path, _ = _checkpoint(tmp_path / 'work.ack')
    destination = tmp_path / 'restore'
    with pytest.raises(ck.CheckpointBlocked, match='encrypted quarantine'):
        ck.restore(path, destination, CIPHER, [], expected_profile='work',
                   replay_privacy=lambda _: None)
    assert not destination.exists()


def test_recovery_status_requires_current_stream_recovery_authority(tmp_path, monkeypatch):
    from catchup_core.recovery_access import WorkRecoveryAccess
    _, manifest = _checkpoint(tmp_path / 'work.ack')
    monkeypatch.setattr(cli, '_authenticated_recovery', lambda *_: ({}, manifest))
    monkeypatch.setattr(cli, '_work_privacy', lambda *_: object())
    monkeypatch.setattr(cli, '_cipher', lambda *_args, **_kwargs: CIPHER)
    def unavailable(*_args):
        raise ck.CheckpointBlocked('current stream recovery authority is unavailable')
    monkeypatch.setattr(WorkRecoveryAccess, 'authorize_checkpoint', unavailable)
    monkeypatch.setattr(cli, 'collect_escrow', lambda *_args, **_kwargs: {})
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(['recovery', str(tmp_path / 'work.ack')], out, err,
                   local=tmp_path / 'local', passphrase=lambda _: 'synthetic')

    assert code == 20
    assert not out.getvalue()
    assert 'stream' in err.getvalue()
    assert 'synthetic secret name' not in err.getvalue()


def test_work_diff_reports_missing_streams_or_refuses_an_unsupported_host(tmp_path):
    path, _ = _checkpoint(tmp_path / 'work.ack')
    root = tmp_path / 'work'
    root.mkdir()
    (root / 'note.txt').write_bytes(ORDINARY)
    if sys.platform != 'win32':
        with pytest.raises(ck.CheckpointBlocked, match='stream capture is unavailable'):
            ck.diff(path, CIPHER, {'work_state': root})
    else:
        result = ck.diff(path, CIPHER, {'work_state': root})
        assert result['changed'] == [] and result['streams_removed'] == 4

def _public_tree(tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    document = root / 'note.txt'
    document.write_bytes(ORDINARY)
    folder = root / 'folder'
    folder.mkdir()
    Path(str(document) + ':synthetic secret name').write_bytes(b'synthetic secret stream bytes')
    Path(str(document) + ':empty').write_bytes(b'')
    Path(str(folder) + ':directory stream').write_bytes(b'folder bytes')
    Path(str(root) + ':root stream').write_bytes(b'root bytes')
    return root, document


def _public_binding(roots):
    captured = ck._capture_work_streams(roots, {})
    return {'schema': 'artha.work-checkpoint-binding.v2', 'profile': 'work', 'plaintext_allowed': False,
            'identity': 'a' * 64, 'generation': 1, 'head_sha256': 'b' * 64, 'mac': 'c' * 64,
            'members': {key: _ending(path.read_bytes()) for key, path in captured.files.items()},
            'streams': {_key(header): _ending(data) for header, data in captured.streams}}


def _public_create(tmp_path, roots, binding, **kwargs):
    return ck.create('work', roots, tmp_path / 'out', CIPHER, {}, 'synthetic passphrase long enough',
                     now=NOW,
                     lock_path=tmp_path / 'checkpoint.lock', context={'work_binding': binding}, **kwargs)


def _prototype_create(tmp_path, roots, binding, **kwargs):
    return ck._create_work_streams(
        roots, tmp_path / 'out', CIPHER, {}, 'synthetic passphrase long enough', now=NOW,
        lock_path=tmp_path / 'checkpoint.lock', exclude={}, context={'work_binding': binding},
        quiesce=lambda: (), before_publish=kwargs.get('before_publish', lambda: None))


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS policy checkpoint capture')
@pytest.mark.parametrize('mutation', ['exempt', 'primary', 'mandatory'])
def test_endpointdlp_public_capture_preserves_primary_and_reports_only_approved_omission(tmp_path, mutation):
    from catchup_core.work_privacy import STREAM_POLICY
    root, document = _public_tree(tmp_path)
    omitted = Path(str(document) + ':sec.endpointdlp')
    omitted.write_bytes(b'volatile metadata')
    roots = {'work_state': root}
    binding = _public_binding(roots)
    binding.update(schema='artha.work-checkpoint-binding.v3', stream_policy=STREAM_POLICY)
    binding['streams'].pop(_key(_identity(OWNER, 'file', 'sec.endpointdlp')))
    def change():
        if mutation == 'exempt':
            omitted.write_bytes(b'changed metadata after capture')
        elif mutation == 'primary':
            document.write_bytes(b'x' * len(ORDINARY))
        else:
            Path(str(document) + ':synthetic secret name').write_bytes(b'changed mandatory bytes')
    if mutation != 'exempt':
        with pytest.raises(ck.CheckpointBlocked):
            _public_create(tmp_path, roots, binding, before_publish=change)
        assert not list((tmp_path / 'out').glob('*'))
        return
    path = _public_create(tmp_path, roots, binding, before_publish=change)
    manifest = ck.verify(path, CIPHER)
    report = manifest['context']['work_stream_omissions']
    assert report['policy'] == STREAM_POLICY and report['semantics'] == 'observed_names_not_preserved'
    assert report['streams'] == [{**_identity(OWNER, 'file', 'sec.endpointdlp'), 'size': 17}]
    assert manifest['entries'][0]['source_sha256'] == hashlib.sha256(ORDINARY).hexdigest()
    assert len(manifest['streams']) == 4 and all(stream['name'] != 'sec.endpointdlp' for stream in manifest['streams'])
    assert document.read_bytes() == ORDINARY and omitted.read_bytes() == b'changed metadata after capture'


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream capture')
@pytest.mark.parametrize('mutation', ['member', 'stream'])
def test_stream_capture_refuses_mutation_during_final_verification(tmp_path, monkeypatch, mutation):
    root, document = _public_tree(tmp_path)
    roots = {'work_state': root}
    binding = _public_binding(roots)
    verify = ck.verify

    def mutate_after_verification(*args, **kwargs):
        manifest = verify(*args, **kwargs)
        if mutation == 'member':
            document.write_bytes(b'changed synthetic bytes')
        else:
            Path(str(document) + ':synthetic secret name').write_bytes(b'changed synthetic stream')
        return manifest

    monkeypatch.setattr(ck, 'verify', mutate_after_verification)
    with pytest.raises(ck.CheckpointBlocked):
        _prototype_create(tmp_path, roots, binding)
    assert not list((tmp_path / 'out').glob('*'))


def test_public_create_routes_v2_to_generation_capture(tmp_path, monkeypatch):
    root = tmp_path / 'work'
    root.mkdir()
    target = tmp_path / 'synthetic.ack'
    calls = []
    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return target
    monkeypatch.setattr(ck, '_create_work_streams', capture)
    assert _public_create(tmp_path, {'work_state': root},
                          {'schema': 'artha.work-checkpoint-binding.v2'}) == target
    assert len(calls) == 1 and calls[0][1]['exclude'] == {}
    assert not (tmp_path / 'out').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS generation capture')
def test_public_generation_capture_uses_only_native_pinned_member_reads(tmp_path, monkeypatch):
    root, _ = _public_tree(tmp_path)
    roots = {'work_state': root}
    binding = _public_binding(roots)
    monkeypatch.setattr(ck, '_file_sha', lambda *_: pytest.fail('path-based Work content read'))
    path = _public_create(tmp_path, roots, binding)
    manifest = ck.verify(path, CIPHER)
    assert manifest['context']['capture_semantics'] == 'authenticated_work_generation'
    assert len(manifest['streams']) == 4


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS bounded inventory')
@pytest.mark.parametrize('case', ['oversized', 'early-end', 'short-read'])
def test_work_member_inventory_is_bounded_and_handles_short_reads(tmp_path, monkeypatch, case):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineFile
    root = tmp_path / 'work'
    root.mkdir()
    path = root / 'note.txt'
    body = b'x' * (3 * 65536 + 7)
    path.write_bytes(body)
    pinned = ck._capture_work_streams({'work_state': root}, {}).owner_snapshots[OWNER]
    if case == 'oversized':
        pinned = replace(pinned, size=ck.recovery_quarantine.MAX_MEMBER_BYTES + 1)
        with pytest.raises(ck.CheckpointBlocked, match='bound'):
            ck._work_member_entry(OWNER, path, pinned)
    elif case == 'early-end':
        monkeypatch.setattr(WindowsQuarantineFile, 'read_chunk', lambda *_: b'')
        with pytest.raises(ck.CheckpointBlocked, match='ended during inventory'):
            ck._work_member_entry(OWNER, path, pinned)
    else:
        read = WindowsQuarantineFile.read_chunk
        def short(member, size):
            assert 0 < size <= 65536
            return read(member, min(size, 40000))
        monkeypatch.setattr(WindowsQuarantineFile, 'read_chunk', short)
        entry = ck._work_member_entry(OWNER, path, pinned)
        assert entry.size == len(body) and entry.sha256 == _ending(body)['sha256']


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS process interruption')
def test_killed_generation_capture_preserves_ciphertext_and_requires_review(tmp_path):
    root, document = _public_tree(tmp_path)
    roots = {'work_state': root}
    binding = _public_binding(roots)
    marker = tmp_path / 'capture-ready'
    code = '''
import json,sys,time
from pathlib import Path
from datetime import datetime,UTC
from catchup_core import checkpoint as c
from catchup_core.crypto import EnvelopeCipher,StaticKeyProvider
base=Path(sys.argv[1])
def pause():
 (base/'capture-ready').write_text('ready')
 time.sleep(120)
c.create('work',{'work_state':base/'work'},base/'out',
 EnvelopeCipher(StaticKeyProvider({1:b's'*32})),{},'synthetic passphrase long enough',
 now=datetime.now(UTC),lock_path=base/'checkpoint.lock',
 context={'work_binding':json.loads(sys.argv[2])},before_publish=pause)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'scripts'))
    child = subprocess.Popen([sys.executable, '-c', code, str(tmp_path), json.dumps(binding)],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 45
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), 'synthetic capture did not reach the interruption point'
        child.kill()
        child.wait(timeout=15)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=15)
    partial = list((tmp_path / 'out').glob('.*.ack.part'))
    assert len(partial) == 1 and not list((tmp_path / 'out').glob('*.ack'))
    assert ORDINARY not in partial[0].read_bytes()
    assert document.read_bytes() == ORDINARY
    with pytest.raises(ck.CheckpointBlocked, match='interrupted checkpoint'):
        _public_create(tmp_path, roots, binding)
    assert partial[0].exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS write-once publication')
def test_generation_capture_preserves_a_separate_process_publication_winner(tmp_path, monkeypatch):
    root, _ = _public_tree(tmp_path)
    roots = {'work_state': root}
    link = ck.os.link
    targets = []
    def competitor(source, target):
        subprocess.run([sys.executable, '-c',
                        'from pathlib import Path;import sys;'
                        'p=Path(sys.argv[1]);p.open("xb").write(b"synthetic competitor")', str(target)],
                       check=True, timeout=30)
        targets.append(target)
        return link(source, target)
    monkeypatch.setattr(ck.os, 'link', competitor)
    with pytest.raises(ck.CheckpointBlocked, match='write-once'):
        _public_create(tmp_path, roots, _public_binding(roots))
    assert targets[0].read_bytes() == b'synthetic competitor'
    assert not list((tmp_path / 'out').glob('.*.ack.part'))


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS generation capture')
@pytest.mark.parametrize('mutation', ['primary', 'stream', 'new-stream'])
def test_late_separate_process_change_cannot_change_the_archived_generation(tmp_path, monkeypatch, mutation):
    root, document = _public_tree(tmp_path)
    roots = {'work_state': root}
    binding = _public_binding(roots)
    link = ck.os.link
    def publish_after_late_write(source, target):
        result = subprocess.run([sys.executable, '-c',
            'from pathlib import Path;import sys;'
            'p=Path(sys.argv[1]);p.write_bytes(b"later source generation")',
            str(document if mutation == 'primary' else Path(str(document) + ':' + (
                'synthetic secret name' if mutation == 'stream' else 'new stream')))],
            capture_output=True, timeout=30)
        assert result.returncode == 0
        return link(source, target)
    monkeypatch.setattr(ck.os, 'link', publish_after_late_write)
    path = _public_create(tmp_path, roots, binding)
    manifest = ck.verify(path, CIPHER)
    assert manifest['context']['work_binding'] == binding
    assert manifest['entries'][0]['sha256'] == _ending(ORDINARY)['sha256']
    assert len(manifest['streams']) == 4
    assert ck.diff(path, CIPHER, roots)['changed' if mutation == 'primary' else (
        'streams_changed' if mutation == 'stream' else 'streams_added')]


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream capture')
def test_prototype_create_writes_authenticated_ack3_with_file_directory_and_root_streams(tmp_path):
    root, _ = _public_tree(tmp_path)
    roots = {'work_state': root}
    path = _prototype_create(tmp_path, roots, _public_binding(roots))

    raw = path.read_bytes()
    assert raw.startswith(b'ACK3')
    assert b'synthetic secret name' not in raw and b'synthetic secret stream bytes' not in raw
    manifest = ck.verify(path, CIPHER)
    assert manifest['schema'] == 'artha.checkpoint.v3' and manifest['roots'] == ['work_state']
    assert [entry['path'] for entry in manifest['entries']] == [OWNER]
    assert {(entry['owner'], entry['owner_kind'], entry['name'], entry['size'])
            for entry in manifest['streams']} == {
        (OWNER, 'file', 'synthetic secret name', 29), (OWNER, 'file', 'empty', 0),
        ('work_state/folder', 'directory', 'directory stream', 12),
        ('work_state', 'directory', 'root stream', 10)}
    assert not list((tmp_path / 'out').glob('.*.part'))


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream capture')
@pytest.mark.parametrize('defect', ['member', 'stream', 'missing-stream'])
def test_prototype_create_refuses_a_binding_that_differs_from_the_capture(tmp_path, defect):
    root, _ = _public_tree(tmp_path)
    roots = {'work_state': root}
    binding = _public_binding(roots)
    if defect == 'member':
        binding['members'][OWNER]['sha256'] = '0' * 64
    elif defect == 'stream':
        next(iter(binding['streams'].values()))['size'] += 1
    else:
        binding['streams'].popitem()
    with pytest.raises(ck.CheckpointError):
        _prototype_create(tmp_path, roots, binding)
    assert not list((tmp_path / 'out').glob('*'))


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream capture')
@pytest.mark.parametrize('mutation', ['stream', 'member', 'new-stream'])
def test_prototype_create_refuses_mutation_before_publication(tmp_path, mutation):
    root, document = _public_tree(tmp_path)
    roots = {'work_state': root}

    def mutate():
        if mutation == 'stream':
            Path(str(document) + ':synthetic secret name').write_bytes(b'changed')
        elif mutation == 'member':
            document.write_bytes(b'changed synthetic bytes!')
        else:
            Path(str(root / 'folder') + ':late').write_bytes(b'late')

    with pytest.raises(ck.CheckpointBlocked) as caught:
        _prototype_create(tmp_path, roots, _public_binding(roots), before_publish=mutate)
    assert 'synthetic secret' not in str(caught.value)
    assert not list((tmp_path / 'out').glob('*'))


def test_public_create_refuses_unknown_work_binding_schema(tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    with pytest.raises(ck.CheckpointBlocked):
        _public_create(tmp_path, {'work_state': root}, {'schema': 'artha.work-checkpoint-binding.v9'})
    assert not (tmp_path / 'out').exists()


def test_public_create_keeps_personal_checkpoints_in_the_legacy_format(tmp_path):
    root = tmp_path / 'personal'
    root.mkdir()
    (root / 'note.txt').write_bytes(ORDINARY)
    path = ck.create('personal', {'state': root}, tmp_path / 'out', CIPHER, {}, 'synthetic passphrase long enough',
                     now=NOW,
                     lock_path=tmp_path / 'checkpoint.lock')
    assert path.read_bytes().startswith(b'ACK2')
    assert ck.verify(path, CIPHER)['schema'] == 'artha.checkpoint.v2'
