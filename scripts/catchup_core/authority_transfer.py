"""Queue-first authority changes: durable intent, ledger promotion, queue confirmation.

The two SQLite stores cannot share a commit. Pending queue metadata and the owner
pause therefore precede promotion; interrupted transitions always deny dispatch.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import importlib
import json
import os
import secrets
import socket
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from catchup_core.backup import atomic_bytes
from catchup_core.session_lock import installation_fence, require_active_installation, session_lock

if TYPE_CHECKING:
    from catchup_core.crypto import EnvelopeCipher
    from catchup_core.ledger import Ledger

_SCHEMA = 'artha.authority-transfer.v1'
_FIELDS = {'schema', 'transfer_id', 'ledger_path', 'generation', 'host', 'former_generation', 'former_host',
           'promoted_at', 'former_host_revoked_at', 'proof_kind', 'checkpoint_sha256', 'checkpoint_generation', 'actor',
           'retirement_receipt_sha256', 'former_host_key_fingerprint', 'profile'}
# Verified: the former host signed a retirement receipt that the new host checked. The two others are the owner's
# word only: nothing proves the former host stopped, so they carry the label in the recorded proof kind.
VERIFIED_PROOF = 'verified_retirement_receipt'
UNVERIFIED_QUIESCENCE = 'unverified_owner_attestation_quiescence'
UNVERIFIED_REVOCATION = 'unverified_owner_attestation_credential_revocation'
_RETIREMENT_SCHEMA = 'artha.authority-retirement.v1'
_RETIREMENT_FIELDS = {'schema', 'profile', 'former_host', 'former_generation', 'new_host', 'checkpoint_sha256',
                      'issued_at', 'nonce', 'public_key', 'signature'}
_RETIREMENT_MAX_BYTES = 16 * 1024
_PROFILES = ('personal', 'work')


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != 'transfer_id'},
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_receipt(value: Any) -> dict[str, Any]:  # noqa: PLR0912 - one flat field-by-field validator
    """Reject malformed journal records before they can authorize a writer."""
    if not isinstance(value, dict) or set(value) != _FIELDS or value.get('schema') != _SCHEMA:
        raise ValueError('invalid authority transfer receipt')
    for key in ('host', 'former_host', 'ledger_path', 'promoted_at', 'checkpoint_generation', 'actor'):
        if not isinstance(value[key], str) or not value[key].strip() or value[key] != value[key].strip():
            raise ValueError('invalid authority transfer identity')
    if (type(value['generation']) is not int or type(value['former_generation']) is not int
            or value['former_generation'] < 1 or value['generation'] != value['former_generation'] + 1
            or value['host'] == value['former_host'] or not Path(value['ledger_path']).is_absolute()):
        raise ValueError('invalid authority transfer epoch')
    if value['profile'] != 'personal':
        raise ValueError('invalid authority transfer profile')
    if value['proof_kind'] not in (VERIFIED_PROOF, UNVERIFIED_QUIESCENCE, UNVERIFIED_REVOCATION):
        raise ValueError('invalid authority transfer attestation')
    bound = (value['retirement_receipt_sha256'], value['former_host_key_fingerprint'])
    if value['proof_kind'] == VERIFIED_PROOF:
        if not all(_is_digest(item) for item in bound):
            raise ValueError('invalid authority retirement binding')
    elif bound != (None, None):
        raise ValueError('invalid authority retirement binding')
    revoked = value['promoted_at'] if value['proof_kind'] == UNVERIFIED_REVOCATION else None
    if value['former_host_revoked_at'] != revoked:
        raise ValueError('invalid authority revocation attestation')
    try:
        stamp = datetime.fromisoformat(value['promoted_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('missing timestamp zone')
        if len(value['checkpoint_sha256']) != 64 or len(bytes.fromhex(value['checkpoint_sha256'])) != 32:
            raise ValueError('invalid digest')
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid authority transfer timestamp or checkpoint digest') from exc
    if value['transfer_id'] != _digest(value):
        raise ValueError('invalid authority transfer binding')
    return value


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _epoch_matches(state: dict[str, Any], receipt: dict[str, Any]) -> bool:
    return all(state.get(k) == receipt[k] for k in ('generation', 'host', 'promoted_at', 'former_host_revoked_at'))


def require_dispatch_authority(db: sqlite3.Connection, *, host: str | None = None) -> None:
    """Metadata-only worker fence, including the promoted ledger's current epoch."""
    try:
        marker = retired_marker(db)
        columns = {row[1] for row in db.execute('PRAGMA table_info(queue_authority)')}
        retired = db.execute('SELECT retirement_json FROM queue_authority WHERE singleton_key=1').fetchone() \
            if 'retirement_json' in columns else None
        if (marker is not None and _marker_present(marker)) or (retired is not None and retired[0] is not None):
            raise ValueError('this host issued a retirement receipt and may not dispatch')
        row = db.execute('SELECT generation,host,promoted_at,former_host_revoked_at,transfer_pending,transfer_json '
                         'FROM queue_authority WHERE singleton_key=1').fetchone()
        if row is None:
            raise ValueError('authority metadata missing')
        generation, owner, promoted, revoked, pending, raw = tuple(row)
        if type(generation) is not int or generation < 1 or pending not in (0, 1):
            raise ValueError('invalid authority metadata')
        if pending:
            raise ValueError('authority transfer pending; effects remain paused')
        current_host = host or (os.environ.get('ARTHA_HOST') or socket.gethostname().split('.')[0]).strip()
        if owner and owner != current_host:
            raise ValueError('host is not the active authority host')
        if generation == 1 and raw is None:
            return
        receipt = validate_receipt(json.loads(raw))
        if not _epoch_matches({'generation': generation, 'host': owner, 'promoted_at': promoted,
                               'former_host_revoked_at': revoked}, receipt):
            raise ValueError('queue authority epoch differs from transfer receipt')
        ledger_path = Path(receipt['ledger_path'])
        require_active_installation(ledger_path)
        with closing(sqlite3.connect(ledger_path.as_uri() + '?mode=ro', uri=True)) as ledger:
            epoch = ledger.execute('SELECT generation,host,promoted_at,former_host_revoked_at '
                                   'FROM authority_generation ORDER BY generation DESC LIMIT 1').fetchone()
        if epoch is None or tuple(epoch) != (generation, owner, promoted, revoked):
            raise ValueError('ledger authority epoch differs from queue')
    except (sqlite3.Error, TypeError, json.JSONDecodeError, OSError) as exc:
        raise ValueError('authority metadata cannot be verified') from exc


def retired_marker(db: sqlite3.Connection) -> Path | None:
    """Sibling file of the queue database that records a signed retirement; None for an in-memory queue."""
    for _, name, file in db.execute('PRAGMA database_list').fetchall():
        if name == 'main':
            return Path(file + '.retired') if file else None
    return None


def _marker_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def key_fingerprint(public: Ed25519PublicKey) -> str:
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()


def _signed_bytes(receipt: dict[str, Any]) -> bytes:
    return json.dumps({k: v for k, v in receipt.items() if k != 'signature'}, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def verify_retirement_receipt(  # noqa: PLR0912 - each signed binding is checked explicitly
                              raw: bytes | str, *, pinned_fingerprint: str, profile: str, former_host: str,
                              former_generation: int, new_host: str, checkpoint_sha256: str) -> dict[str, Any]:
    """Check a retirement receipt against a key fingerprint the owner pinned and the exact transfer it must serve.

    A receipt for another profile, epoch, former host, target host or checkpoint is refused, so an old or
    foreign receipt cannot be replayed. Raises ValueError with a plain reason.
    """
    data = raw.encode('utf-8') if isinstance(raw, str) else raw
    if len(data) > _RETIREMENT_MAX_BYTES:
        raise ValueError('retirement receipt is too large')
    try:
        receipt = json.loads(data, object_pairs_hook=_no_duplicates)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('retirement receipt is malformed') from None
    if not isinstance(receipt, dict) or set(receipt) != _RETIREMENT_FIELDS or receipt['schema'] != _RETIREMENT_SCHEMA:
        raise ValueError('retirement receipt is malformed')
    texts = ('profile', 'former_host', 'new_host', 'checkpoint_sha256', 'issued_at', 'nonce', 'public_key', 'signature')
    if (type(receipt['former_generation']) is not int
            or not all(isinstance(receipt[k], str) and receipt[k] for k in texts)):
        raise ValueError('retirement receipt is malformed')
    if (receipt['former_generation'] < 1 or receipt['profile'] not in _PROFILES
            or receipt['former_host'] == receipt['new_host'] or not _is_digest(receipt['checkpoint_sha256'])):
        raise ValueError('retirement receipt is malformed')
    try:
        stamp = datetime.fromisoformat(receipt['issued_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or len(receipt['nonce']) != 32 or len(bytes.fromhex(receipt['nonce'])) != 16:
            raise ValueError('invalid retirement timestamp or nonce')
    except ValueError:
        raise ValueError('retirement receipt is malformed') from None
    try:
        public_bytes = base64.b64decode(receipt['public_key'], validate=True)
        signature = base64.b64decode(receipt['signature'], validate=True)
        public = Ed25519PublicKey.from_public_bytes(public_bytes)
    except (binascii.Error, ValueError):
        raise ValueError('retirement receipt key or signature is malformed') from None
    if not _is_digest(pinned_fingerprint) or not hmac.compare_digest(key_fingerprint(public), pinned_fingerprint):
        raise ValueError('retirement receipt key is not the pinned former host key')
    try:
        public.verify(signature, _signed_bytes(receipt))
    except InvalidSignature:
        raise ValueError('retirement receipt signature is invalid') from None
    if receipt['profile'] != profile:
        raise ValueError('retirement receipt is for another profile')
    if receipt['former_generation'] != former_generation:
        raise ValueError('retirement receipt is for another authority epoch')
    if receipt['former_host'] != former_host:
        raise ValueError('retirement receipt is from a host that is not the current authority host')
    if receipt['new_host'] != new_host:
        raise ValueError('retirement receipt names another new host')
    if receipt['checkpoint_sha256'] != checkpoint_sha256:
        raise ValueError('retirement receipt is for another checkpoint')
    return receipt


def _local_host() -> str:
    return (os.environ.get('ARTHA_HOST') or socket.gethostname().split('.')[0]).strip()


def _keyring_signer() -> Ed25519PrivateKey:
    """This machine's retirement key, kept in the OS credential store and never part of any checkpoint."""
    from catchup_core.crypto import system_keyring  # noqa: PLC0415
    backend = system_keyring()
    stored = backend.get_password('artha-retirement-key', 'host')
    if stored is None:
        key = Ed25519PrivateKey.generate()
        backend.set_password('artha-retirement-key', 'host', base64.b64encode(key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())).decode('ascii'))
        return key
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(stored, validate=True))


_TRANSFER_PAUSE = b'Actions paused for owner-reviewed authority transfer.\n'


def _pause_record(record: Path) -> dict[str, bool]:
    from catchup_core.ledger import PermissionDenied  # noqa: PLC0415
    try:
        with record.open('rb') as handle:
            raw = handle.read(1025)
        if len(raw) > 1024:
            raise ValueError('pause provenance is too large')
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {'preexisting'} or type(value['preexisting']) is not bool:
            raise ValueError('invalid pause provenance')
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise PermissionDenied('transfer pause provenance cannot be verified; keep effects paused') from exc


def _preserve_pause(workspace: Path) -> None:
    """Remember existing owner pauses before publishing a durable transfer pause."""
    pause, record = workspace / 'config/actions.paused', workspace / 'config/actions.transfer-pause.json'
    if record.exists():
        _pause_record(record)
    else:
        atomic_bytes(record, json.dumps({'preexisting': pause.exists()}).encode('ascii'))
    if not pause.exists():
        atomic_bytes(pause, _TRANSFER_PAUSE)


def _release_pause(workspace: Path) -> None:
    pause, record = workspace / 'config/actions.paused', workspace / 'config/actions.transfer-pause.json'
    preexisting = _pause_record(record)['preexisting'] if record.exists() else False
    # A pause changed after transfer preparation belongs to the owner as well.
    if not preexisting and pause.exists() and record.exists():
        with pause.open('rb') as handle:
            preexisting = handle.read(len(_TRANSFER_PAUSE) + 1) != _TRANSFER_PAUSE
    if not preexisting:
        pause.unlink(missing_ok=True)
    record.unlink(missing_ok=True)


def _read_retirement(path: Path) -> bytes:
    with path.open('rb') as handle:
        return handle.read(_RETIREMENT_MAX_BYTES + 1)


def issue_retirement_receipt(  # noqa: PLR0912, PLR0915 - ordered retirement effects and writer fence
                             ledger: Ledger, workspace: Path, *, actor: str, new_host: str,
                             checkpoint_path: Path | str, profile: str = 'personal',
                             signer: Ed25519PrivateKey | None = None) -> dict[str, Any]:
    """Run on the active authority host: stop dispatch here for good, then sign that this host has retired.

    The receipt names the profile, the epoch being left, the receiving host and the checkpoint the receiver
    restores. After this call the host's own queue refuses every dispatch (the retired marker).
    """
    queue_type = importlib.import_module("action_queue").ActionQueue
    from catchup_core.ledger import PermissionDenied  # noqa: PLC0415
    ledger._require_unlocked()
    if actor != ledger._owner:
        raise PermissionDenied('retiring authority requires the local owner')
    target = new_host.strip() if isinstance(new_host, str) else ''
    if not target:
        raise ValueError('retirement requires a new host')
    if profile != 'personal':
        raise PermissionDenied('authority retirement requires personal installation preservation; Work is separate')
    try:
        with Path(checkpoint_path).expanduser().open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    except OSError as exc:
        raise PermissionDenied('retirement checkpoint cannot be read') from exc
    require_active_installation(workspace, ledger._path)
    with installation_fence(ledger._path.parent.parent), session_lock(ledger._path.with_suffix('.authority.lock')):
        queue = queue_type(workspace)
        try:
            current, queued = ledger.authority_state(), queue.authority_state()
            if (current['host'] != _local_host() or queued['transfer_pending']
                    or queued['generation'] != current['generation']
                    or queued['host'] not in (None, current['host'])):
                raise PermissionDenied('only the active authority host with settled queue and ledger epochs can retire')
            if current['host'] == target:
                raise PermissionDenied('retirement requires a distinct receiving host')
            marker = retired_marker(queue._conn)
            if marker is None:
                raise PermissionDenied('retirement needs a file-backed action queue')
            wanted = {'profile': profile, 'former_host': current['host'], 'former_generation': current['generation'],
                      'new_host': target, 'checkpoint_sha256': digest}
            stored = queued['retirement']
            if _marker_present(marker):
                marked = json.loads(_read_retirement(marker))
                if stored is not None and stored != marked:
                    raise PermissionDenied('retirement records disagree; this installation remains retired')
                stored = marked
            if stored is not None:
                if not isinstance(stored, dict) or any(stored.get(k) != v for k, v in wanted.items()):
                    raise PermissionDenied('this host already retired for a different transfer')
                # Repair either durable record from the other after a crash or missing sibling file.
                _preserve_pause(workspace)
                atomic_bytes(marker, json.dumps(stored, sort_keys=True).encode('utf-8'))
                with queue._conn:
                    queue._conn.execute('UPDATE queue_authority SET retirement_json=? WHERE singleton_key=1',
                                        (json.dumps(stored, sort_keys=True),))
                return dict(stored)
            if ledger._cipher is None:
                raise PermissionDenied('retirement requires the ledger encryption key')
            manifest = _verify_checkpoint(Path(checkpoint_path).expanduser(), ledger._cipher, digest)
            if manifest.get('profile') != 'personal':
                raise PermissionDenied('authority retirement requires a personal installation checkpoint')
            key = signer or _keyring_signer()
            # The worker claims dispatch under BEGIN IMMEDIATE too. Keep that same writer fence from the
            # quiescence check through marker publication, so a concurrent claim cannot slip between them.
            queue._conn.execute('BEGIN IMMEDIATE')
            try:
                if queue._conn.execute("SELECT 1 FROM actions WHERE status='executing' LIMIT 1").fetchone():
                    raise PermissionDenied('executing actions contradict retirement; reconcile them first')
                _phase('retirement_quiesced')
                _preserve_pause(workspace)
                _phase('retirement_paused')
                receipt: dict[str, Any] = {
                    'schema': _RETIREMENT_SCHEMA, **wanted, 'issued_at': ledger._clock(),
                    'nonce': secrets.token_hex(16),
                    'public_key': base64.b64encode(key.public_key().public_bytes(
                        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode('ascii')}
                receipt['signature'] = base64.b64encode(key.sign(_signed_bytes(receipt))).decode('ascii')
                atomic_bytes(marker, json.dumps(receipt, sort_keys=True).encode('utf-8'))
                _phase('retirement_marker_persisted')
                queue._conn.execute('UPDATE queue_authority SET retirement_json=? WHERE singleton_key=1',
                                    (json.dumps(receipt, sort_keys=True),))
                queue._conn.commit()
                _phase('retirement_persisted')
                return receipt
            finally:
                queue._conn.rollback()
        finally:
            queue.close()


def _verify_checkpoint(path: Path, cipher: EnvelopeCipher, digest: str) -> dict[str, Any]:
    from catchup_core.policy import verify_preservation_checkpoint  # noqa: PLC0415
    return verify_preservation_checkpoint(path, cipher, expected_sha256=digest)


def _phase(_name: str) -> None:
    """Effect boundary used by process-termination fault tests."""


def transfer_authority(  # noqa: PLR0912, PLR0915 - one ordered, crash-resumable protocol
                       ledger: Ledger, new_host: str, *, actor: str, checkpoint_path: Path | str,
                       workspace: Path, expected_generation: int, cipher: EnvelopeCipher | None = None,
                       former_host_quiesced: bool = False, credentials_revoked: bool = False,
                       retirement_receipt: Path | str | None = None,
                       former_host_key_fingerprint: str | None = None,
                       profile: str = 'personal') -> dict[str, Any]:
    queue_type = importlib.import_module("action_queue").ActionQueue
    from catchup_core.ledger import PermissionDenied  # noqa: PLC0415
    ledger._require_unlocked()
    if actor != ledger._owner:
        raise PermissionDenied('authority transfer requires the local owner')
    if type(expected_generation) is not int or expected_generation < 1:
        raise PermissionDenied('expected authority generation must be a positive integer')
    if type(former_host_quiesced) is not bool or type(credentials_revoked) is not bool:
        raise PermissionDenied('authority transfer attestations must be booleans')
    attested = former_host_quiesced or credentials_revoked
    if retirement_receipt is not None and attested:
        raise PermissionDenied('use either a signed retirement receipt or an unverified owner attestation, not both')
    if retirement_receipt is None and not attested:
        raise PermissionDenied('authority transfer requires a signed retirement receipt from the former host, or '
                               'an explicit unverified owner attestation of quiescence or credential revocation')
    if retirement_receipt is not None and not _is_digest(former_host_key_fingerprint):
        raise PermissionDenied('a retirement receipt needs the former host key fingerprint pinned by the owner')
    if profile != 'personal':
        raise PermissionDenied('authority promotion requires personal installation preservation; Work is separate')
    target = new_host.strip() if isinstance(new_host, str) else ''
    if not target:
        raise ValueError('new authority host is required')
    active_cipher = cipher or ledger._cipher
    if active_cipher is None:
        raise PermissionDenied('authority transfer requires the ledger encryption key')
    source = Path(checkpoint_path).expanduser()
    try:
        with source.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    except OSError as exc:
        raise PermissionDenied('authority transfer checkpoint cannot be read') from exc
    receipt_sha = None
    raw_receipt = None
    if retirement_receipt is not None:
        try:
            raw_receipt = _read_retirement(Path(retirement_receipt).expanduser())
        except OSError as exc:
            raise PermissionDenied('retirement receipt cannot be read') from exc
        receipt_sha = hashlib.sha256(raw_receipt).hexdigest()
        proof = VERIFIED_PROOF
    else:
        proof = UNVERIFIED_REVOCATION if credentials_revoked else UNVERIFIED_QUIESCENCE
    require_active_installation(workspace, ledger._path)
    with installation_fence(ledger._path.parent.parent), session_lock(ledger._path.with_suffix('.authority.lock')):
        queue = queue_type(workspace)
        try:
            current, queued = ledger.authority_state(), queue.authority_state()
            previous = queued['transfer']
            if previous is not None:
                receipt = validate_receipt(previous)
                same = (receipt['former_generation'] == expected_generation and receipt['host'] == target
                        and receipt['checkpoint_sha256'] == digest and receipt['actor'] == actor
                        and receipt['proof_kind'] == proof and receipt['ledger_path'] == str(ledger._path.resolve())
                        and receipt['retirement_receipt_sha256'] == receipt_sha
                        and receipt['former_host_key_fingerprint'] ==
                            (former_host_key_fingerprint if receipt_sha else None)
                        and receipt['profile'] == profile)
                if same:
                    if not queued['transfer_pending']:
                        if not _epoch_matches(current, receipt):
                            raise PermissionDenied('completed transfer has inconsistent ledger epoch')
                        return receipt
                elif queued['transfer_pending'] or expected_generation != current['generation']:
                    raise PermissionDenied('conflicting pending or stale authority transfer')
                else:
                    receipt = {}  # A subsequent transfer may use the current generation.
            else:
                receipt = {}
            if not receipt:
                if current['generation'] != expected_generation:
                    raise PermissionDenied('expected authority generation is stale')
                if not current['host'] or current['host'] == target:
                    raise PermissionDenied('authority transfer requires a distinct known former host')
                if queued['generation'] != expected_generation or queued['host'] not in (None, current['host']):
                    raise PermissionDenied('queue and ledger authority epochs differ')
                manifest = _verify_checkpoint(source, active_cipher, digest)
                if manifest.get('profile') != 'personal':
                    raise PermissionDenied('authority promotion requires a personal installation checkpoint')
                if raw_receipt is not None:
                    try:
                        verify_retirement_receipt(
                            raw_receipt, pinned_fingerprint=str(former_host_key_fingerprint), profile=profile,
                            former_host=current['host'], former_generation=expected_generation,
                            new_host=target, checkpoint_sha256=digest)
                    except ValueError as exc:
                        raise PermissionDenied(str(exc)) from None
                now = ledger._clock()
                receipt = {'schema': _SCHEMA, 'ledger_path': str(ledger._path.resolve()),
                    'generation': expected_generation + 1, 'host': target, 'former_generation': expected_generation,
                    'former_host': current['host'], 'promoted_at': now,
                    'former_host_revoked_at': now if proof == UNVERIFIED_REVOCATION else None, 'proof_kind': proof,
                    'checkpoint_sha256': digest, 'checkpoint_generation': str(manifest['generation']), 'actor': actor,
                    'retirement_receipt_sha256': receipt_sha,
                    'former_host_key_fingerprint': former_host_key_fingerprint if receipt_sha else None,
                    'profile': profile}
                receipt['transfer_id'] = _digest(receipt)
                validate_receipt(receipt)
            _preserve_pause(workspace)
            _phase('paused')
            queue.prepare_authority_transfer(receipt)
            _phase('queue_prepared')
            with ledger.transaction() as conn:
                current = ledger.authority_state()
                if not _epoch_matches(current, receipt):
                    if current['generation'] != expected_generation or current['host'] != receipt['former_host']:
                        raise PermissionDenied('ledger authority epoch changed during transfer')
                    conn.execute('INSERT INTO authority_generation(generation,host,promoted_at,former_host_revoked_at) '
                                 'VALUES(?,?,?,?)', tuple(receipt[k] for k in
                                 ('generation', 'host', 'promoted_at', 'former_host_revoked_at')))
            _phase('ledger_promoted')
            queue.finalize_authority_transfer(receipt)
            _phase('queue_finalized')
            return receipt
        finally:
            queue.close()


def resume_effects(ledger: Ledger, workspace: Path, *, actor: str) -> None:
    queue_type = importlib.import_module("action_queue").ActionQueue
    from catchup_core.ledger import PermissionDenied  # noqa: PLC0415
    ledger._require_unlocked()
    if actor != ledger._owner:
        raise PermissionDenied('resuming effects requires the local owner')
    with installation_fence(ledger._path.parent.parent), session_lock(ledger._path.with_suffix('.authority.lock')):
        queue = queue_type(workspace)
        try:
            state = queue.authority_state()
            if state['transfer_pending']:
                receipt = validate_receipt(state['transfer'])
                if not _epoch_matches(ledger.authority_state(), receipt):
                    raise PermissionDenied('authority transfer incomplete; rerun the original transfer')
                queue.finalize_authority_transfer(receipt)
            current = ledger.authority_state()
            if (state['generation'] != current['generation']
                    or state['host'] not in (None, current['host'])):
                raise PermissionDenied('queue and ledger authority epochs differ; keep effects paused')
            try:
                require_dispatch_authority(queue._conn)
            except ValueError as exc:
                raise PermissionDenied(str(exc)) from exc
            _release_pause(workspace)
        finally:
            queue.close()
