"""Trusted local release and disclosure policy. Model output never supplies authority."""
from __future__ import annotations

import hashlib
import io
import json
import socket
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import closing, nullcontext
from pathlib import Path
from typing import Any, BinaryIO, cast

from catchup_core.contracts import CatchupRequest, ExecutionMode
from catchup_core.crypto import EnvelopeCipher
from catchup_core.ledger import Ledger, PermissionDenied


def validate_local_authority(request: CatchupRequest, config: Mapping[str, Any], *, viewer: str,
                             hostname: str | None = None) -> None:
    """Local safety controls authenticate authority independently of pilot quality evidence."""
    from dataclasses import replace  # noqa: PLC0415
    validate_release(replace(request, mode=ExecutionMode.SHADOW), config, viewer=viewer, domains=())
    if request.mode is ExecutionMode.SHADOW:
        return
    host = hostname or socket.gethostname().split(".")[0]
    release = config.get("release", {})
    authority = config.get("authority_host") or (release.get("authority_host") if isinstance(release, dict) else None)
    if authority != host or request.host != host:
        raise PermissionDenied("local safety control requires the designated authority host")


def validate_release(request: CatchupRequest, config: Mapping[str, Any], *, viewer: str,
                     domains: Sequence[str]) -> None:
    if request.actor != viewer:
        raise PermissionDenied("request actor does not match the configured local viewer")
    if request.channel not in ("cli", "vscode", "codex"):
        raise PermissionDenied("this surface is not authorized for personal evidence")
    if request.mode is not ExecutionMode.SHADOW:
        raise PermissionDenied('authoritative cutover is disabled; Light mode runs shadow only')


def evaluation_cohort(config: Mapping[str, Any], domains: Sequence[str], *,
                      extractor_command: str | None = None, workspace: Path | None = None,
                      source_root: Path | None = None) -> dict[str, str]:
    """Pin evaluation inputs independently of release approval and the cutover switch."""
    from catchup_core import CORE_CONTRACT_VERSION  # noqa: PLC0415
    from catchup_core.runtime import _implementation_fingerprint  # noqa: PLC0415
    root = source_root or Path(__file__).resolve().parents[2]
    package = root / "scripts" / "catchup_core"
    def digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    def files(paths: Sequence[Path]) -> str:
        return digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    workspace_inputs: dict[str, str | None] = {}
    for name in ('user_profile.yaml', 'connectors.yaml'):
        try:
            with ((workspace or root) / 'config' / name).open('rb') as handle:
                data = handle.read(_MAX_RELEASE_JSON + 1)
        except FileNotFoundError:
            workspace_inputs[name] = None
        except OSError:
            raise PermissionDenied('evaluation workspace inputs cannot be verified') from None
        else:
            if len(data) > _MAX_RELEASE_JSON:
                raise PermissionDenied('evaluation workspace inputs exceed the size bound')
            workspace_inputs[name] = hashlib.sha256(data).hexdigest()
    command = extractor_command or config.get('extractor_cmd')
    identity = config.get('model_identity')
    model = 'unverified'
    if isinstance(identity, dict) and all(isinstance(identity.get(key), str) and identity[key].strip()
                                         for key in ('provider', 'model', 'revision', 'retention_policy')):
        model = digest(identity)
    if command:
        # The command boundary permits only the bundled inert extractor; its exact bytes are pinned here too.
        model = digest({'provider': 'local', 'model': 'pattern-extractor',
                        'revision': files([package / 'extractor_cmd.py']), 'command': command})
    return {'implementation': _implementation_fingerprint(root),
            'prompts': files(sorted((package / 'prompts').glob('*.md'))),
            'packs': files([package / 'packs' / f'{domain}.yaml' for domain in sorted(domains)]),
            'configuration': digest({'policy': {k: v for k, v in config.items() if k not in {'release', 'cutover'}},
                                     'workspace_inputs': workspace_inputs}),
            'model': model, 'contract': CORE_CONTRACT_VERSION}


SLICE_WAIVABLE = ("cross_domain_and_goal_cases", "document_field_accuracy", "held_out_labeling",
                  "other_platform_observations", "windows_work_recovery", "workload_scenarios")
SLICE_MIN_WINDOW_DAYS = 14


_RELEASE_WORKLOADS = ('normal_cold', 'normal_warm', 'long_gap', 'provider_reset',
                      'retention_1y', 'retention_2y', 'digest_limited')


_MAX_RELEASE_JSON = 16 << 20
_PRESERVATION_SCHEMA = 'artha.installation-preservation.v1'


class _DigestingReader:
    """Hash exactly the bytes consumed by the authenticated reader over its already-open file."""
    def __init__(self, source: BinaryIO):
        self.source = source
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(size)
        self.digest.update(data)
        return data


def _check_installation_scope(manifest: Mapping[str, Any]) -> None:
    context = manifest.get('context', {})
    scope = context.get('preservation') if isinstance(context, Mapping) else None
    if (not isinstance(scope, Mapping) or scope.get('schema') != _PRESERVATION_SCHEMA
            or scope.get('repository_root') != 'repo' or scope.get('runtime_root') != 'local'
            or scope.get('inventory') != 'recursive-fail-closed'):
        raise ValueError('checkpoint does not declare complete installation preservation')
    roots = scope.get('root_names')
    if (not isinstance(roots, list) or any(not isinstance(root, str) or not root for root in roots)
            or len(set(roots)) != len(roots) or not {'repo', 'local'} <= set(roots)):
        raise ValueError('checkpoint installation roots are incomplete or ambiguous')
    entries = manifest['entries']
    paths = {entry['path'] for entry in entries}
    if (any(path.split('/')[0] not in roots for path in paths)
            or 'repo/config/catchup_core.yaml' not in paths
            or not any(path.startswith('repo/state/') for path in paths)
            or not any(entry['kind'] == 'sqlite' and entry['path'] in {
                'local/catchup_core/ledger.db', 'local/catchup_core/shadow/ledger.db'} for entry in entries)):
        raise ValueError('checkpoint lacks repository configuration, State Files or runtime Ledger')
    required_escrow = {'age-key/artha', 'artha-ledger/key.1', 'artha-ledger-ids/key.1'}
    if not required_escrow <= set(manifest.get('escrow_items') or ()):
        raise ValueError('checkpoint lacks required recovery escrow items')


_VERIFY_IN_MEMORY = 8 << 20  # a member database above this is checked from a private temporary file, not memory


class _DatabaseCheck:
    """Collects one database member as it streams past, then runs SQLite's integrity and key checks on it.

    Small images stay in memory.  A larger one spills to an owner-only temporary file that is deleted before
    the check returns, so memory never holds more than the spill threshold.
    """

    def __init__(self) -> None:
        self.size = 0
        self.memory: bytearray | None = bytearray()
        self.spill: Any = None

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.memory is not None and self.size > _VERIFY_IN_MEMORY:
            self.spill = tempfile.NamedTemporaryFile(prefix='artha-verify-', delete=False)  # noqa: SIM115 - closed and unlinked in discard(); mode 0600
            self.spill.write(self.memory)
            self.memory = None
        if self.spill is not None:
            self.spill.write(chunk)
        elif self.memory is not None:
            self.memory.extend(chunk)

    def check(self) -> None:
        try:
            if self.spill is None:
                with closing(sqlite3.connect(':memory:')) as db:
                    db.deserialize(self.memory)  # type: ignore[arg-type]  # the bytearray: no second copy
                    self._verify(db)
                return
            self.spill.close()
            uri = Path(self.spill.name).resolve().as_uri() + '?mode=ro&immutable=1'
            with closing(sqlite3.connect(uri, uri=True)) as db:
                self._verify(db)
        finally:
            self.discard()

    def discard(self) -> None:
        self.memory = None
        if self.spill is not None:
            self.spill.close()
            Path(self.spill.name).unlink(missing_ok=True)
            self.spill = None

    @staticmethod
    def _verify(db: sqlite3.Connection) -> None:
        if (db.execute('PRAGMA integrity_check').fetchone() != ('ok',)
                or db.execute('PRAGMA foreign_key_check').fetchone()):
            raise ValueError('checkpoint database integrity failed')


def _scan_preservation(source: BinaryIO, cipher: EnvelopeCipher) -> dict[str, Any]:
    from catchup_core import checkpoint  # noqa: PLC0415
    current: _DatabaseCheck | None = None

    def finish() -> None:
        nonlocal current
        if current is not None:
            finished, current = current, None
            finished.check()

    def opened(header: dict[str, Any]) -> None:
        nonlocal current
        finish()
        current = _DatabaseCheck() if header['kind'] == 'sqlite' else None

    def collect(_header: dict[str, Any], chunk: bytes) -> None:
        if current is not None:
            current.write(chunk)

    try:
        manifest = checkpoint._scan(source, cipher, collect, opened)
        finish()
    finally:
        if current is not None:
            current.discard()
    if manifest.get('profile') != 'personal':
        raise ValueError('checkpoint is not a personal installation')
    _check_installation_scope(manifest)
    return manifest


def verify_preservation_checkpoint(source: bytes | Path | BinaryIO, cipher: EnvelopeCipher | None = None, *,
                                   expected_sha256: str | None = None) -> dict[str, Any]:
    """Verify approved ACK2 bytes on one pinned handle; memory is bounded by one SQLite image."""
    from catchup_core import checkpoint  # noqa: PLC0415
    try:
        context = closing(io.BytesIO(source)) if isinstance(source, bytes) else (
            source.open('rb') if isinstance(source, Path) else nullcontext(source))
        with context as handle:
            handle.seek(0)
            if handle.read(3) == b'AE1':
                raise PermissionDenied('selective rollback snapshot cannot satisfy full-state preservation; '
                                       'use an ACK2 full-state checkpoint')
            handle.seek(0)
            generation, escrow = checkpoint._read_head(handle)
            if not escrow.startswith(checkpoint._ESCROW_MAGIC) or len(escrow) < 31:
                raise ValueError('checkpoint recovery escrow is missing or malformed')
            handle.seek(0)
            reader = _DigestingReader(handle)
            if cipher is None:
                while reader.read(1 << 20):
                    pass
                manifest: dict[str, Any] = {'generation': generation}
            else:
                manifest = _scan_preservation(cast(BinaryIO, reader), cipher)
            if expected_sha256 is not None and reader.digest.hexdigest() != expected_sha256:
                raise PermissionDenied('rollback snapshot differs from approved bytes')
            return manifest
    except PermissionDenied:
        raise
    except (OSError, ValueError, TypeError, sqlite3.Error, checkpoint.CheckpointError):
        raise PermissionDenied('rollback snapshot could not be decrypted and verified') from None


def authorize_source(ledger: Ledger, subjects: Sequence[str], required: frozenset[str],
                     scope: str, viewer: str, *, operations: Sequence[str] = ("ingest", "disclose", "view")) -> None:
    for subject in subjects:
        if ledger._known_subjects is not None and subject not in ledger._known_subjects:
            raise PermissionDenied("source has an unresolved person; resolve identity before access")
        if subject in required:
            for operation in operations:
                if not ledger.consent_active(subject, operation, viewer, scope=scope):
                    raise PermissionDenied(f"source requires {subject}'s scoped {operation} consent")
