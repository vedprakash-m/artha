"""Explicit, review-bound migration from legacy secret files to the platform store.

Personal and Work inputs and recovery keys stay separate. Originals are replaced
by authenticated ciphertext only after OS records and encrypted recovery verify.
No provider is contacted, and no plaintext staging file is created.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from catchup_core.backup import atomic_bytes
from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider
from catchup_core.session_lock import session_lock
from lib.credentials import CredentialStore

_PERSONAL = {
    'gmail-oauth-token.json': 'google.gmail-oauth-token', 'gmail-token.json': 'google.gmail-oauth-token',
    'gcal-oauth-token.json': 'google.gcal-oauth-token', 'gcal-token.json': 'google.gcal-oauth-token',
    'gdrive-oauth-token.json': 'google.gdrive-oauth-token', 'msgraph-token.json': 'msgraph.personal',
    'msgraph-personal-token.json': 'msgraph.personal',
}
_WORK = {'msgraph-work-token.json': 'msgraph.work', 'msgraph-work-mcp-token.json': 'msgraph.work-mcp'}
_CLIENTS = {'google-client-creds.json': {'gmail-client-id', 'gmail-client-secret'},
            'msgraph-client-creds.json': {'msgraph-client-id'}}


class MigrationError(RuntimeError):
    pass


def _put(target: dict[str, Any], name: str, value: Any) -> None:
    if name in target and target[name] != value:
        raise MigrationError('conflicting legacy credentials require explicit resolution')
    target[name] = value


def _capture(source: Path, work: bool) -> tuple[dict[str, bytes], dict[str, Any], dict[str, str], list[str]]:
    tokens = _WORK if work else _PERSONAL
    client_files = {'msgraph-client-creds.json': {'msgraph-work-client-id'}} if work else _CLIENTS
    files = {}
    for name in sorted(set(tokens) | set(client_files)):
        path = source / name
        if path.is_symlink():
            raise MigrationError('credential symlink inputs require explicit resolution')
        if not path.exists():
            continue
        if not path.is_file() or path.stat().st_size > 1_000_000:
            raise MigrationError('legacy credential file is not bounded')
        files[name] = path.read_bytes()
    return _decode(files, work)


def _decode(files: dict[str, bytes], work: bool) -> tuple[dict[str, bytes], dict[str, Any], dict[str, str], list[str]]:
    tokens = _WORK if work else _PERSONAL
    client_files = {'msgraph-client-creds.json': {'msgraph-work-client-id'}} if work else _CLIENTS
    if set(files) - (set(tokens) | set(client_files)):
        raise MigrationError('credential recovery contains an unknown or different-scope file')
    files = dict(files)
    records: dict[str, Any] = {}
    clients: dict[str, str] = {}
    sealed: list[str] = []
    for name, raw in list(files.items()):
        if raw.startswith(b'AE1'):
            sealed.append(name)
            continue
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise MigrationError('legacy credential JSON is invalid; no values were imported') from None
        if not isinstance(payload, dict):
            raise MigrationError('legacy credential JSON must be a record')
        if name in client_files:
            selected = set(payload) & client_files[name]
            if not selected:
                files.pop(name)
                continue
            if set(payload) - client_files[name]:
                raise MigrationError('mixed-scope client mirror requires explicit separation before migration')
        if name in tokens:
            _put(records, tokens[name], payload)
            if name.startswith(('gmail', 'gcal', 'gdrive')):
                for field, key in (('client_id', 'gmail-client-id'), ('client_secret', 'gmail-client-secret')):
                    if payload.get(field):
                        _put(clients, key, payload[field])
            elif payload.get('_artha_client_id'):
                _put(clients, 'msgraph-work-client-id' if work else 'msgraph-client-id', payload['_artha_client_id'])
        else:
            for key in client_files[name]:
                if payload.get(key):
                    _put(clients, key, payload[key])
    if any(not isinstance(value, str) for value in clients.values()):
        raise MigrationError('legacy client credential has an invalid type')
    return files, records, clients, sealed


def _check_existing(vault: CredentialStore, records: dict[str, Any], clients: dict[str, str]) -> None:
    for name, value in records.items():
        old = vault.read(name)
        if old is not None and old != value:
            raise MigrationError(f'OS store has a different {name} token; it was not overwritten')
    for name, value in clients.items():
        if vault._backend.get_password(name, 'artha') not in (None, value):
            raise MigrationError('OS store already has a different client credential; it was not overwritten')


def _import(vault: CredentialStore, records: dict[str, Any], clients: dict[str, str]) -> None:
    for name, value in records.items():
        vault.write(name, value, replace_existing=False)
    for name, value in clients.items():
        if vault._backend.get_password(name, 'artha') is None:
            vault._backend.set_password(name, 'artha', value)
        if vault._backend.get_password(name, 'artha') != value:
            raise MigrationError('client credential persistence is unverified; originals remain unchanged')
    for name, value in records.items():
        if vault.read(name) != value:
            raise MigrationError('token persistence is unverified; originals remain unchanged')


def _digest(files: dict[str, bytes], work: bool) -> str:
    content = json.dumps({name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}, sort_keys=True)
    return hashlib.sha256((('work' if work else 'personal') + content).encode()).hexdigest()


def plan(source: Path, *, work: bool = False) -> dict[str, Any]:
    files, records, clients, sealed = _capture(source, work)
    return {'scope': 'work' if work else 'personal', 'review_hash': _digest(files, work),
            'files': [{'name': name, 'bytes': len(raw)} for name, raw in files.items()],
            'records': sorted(records), 'client_credentials': sorted(clients), 'already_sealed': sealed}


def apply(source: Path, local: Path, review_hash: str, *, backend: Any = None,
          work: bool = False) -> dict[str, Any]:
    from catchup_core.session_lock import installation_fence, require_active_installation  # noqa: PLC0415
    require_active_installation(source, local)
    scope = 'work' if work else 'personal'
    with (
        installation_fence(local),
        session_lock(local / 'credential-locks' / f'migration-{scope}.lock'),
    ):
        files, records, clients, sealed = _capture(source, work)
        if _digest(files, work) != review_hash:
            raise MigrationError('legacy inputs changed; review the new plan')
        vault = CredentialStore(backend=backend)
        backend = vault._backend
        if sealed:
            existing_cipher = EnvelopeCipher(KeyringKeyProvider(
                f'artha-credential-recovery-{scope}', backend=backend, create=False))
            for name in sealed:
                existing_cipher.decrypt(files[name], aad=('artha.credential.source:' + name).encode())
        _check_existing(vault, records, clients)
        pending = {name: raw for name, raw in files.items() if name not in sealed}
        if not pending:
            return {'scope': scope, 'records': [], 'sealed': [], 'already_sealed': sealed, 'backup': None}
        cipher = EnvelopeCipher(KeyringKeyProvider(
            f'artha-credential-recovery-{scope}', backend=backend, create=not bool(sealed)))
        aad = f'artha.credentials.migration.v1:{scope}:{review_hash}'.encode()
        payload = json.dumps({'schema': 'artha.credentials.migration.v1', 'scope': scope,
                              'files': {name: base64.b64encode(raw).decode() for name, raw in pending.items()}},
                             sort_keys=True).encode()
        backup = local / 'credential-recovery' / f'{scope}-{review_hash}.ae1'
        if backup.exists():
            if cipher.decrypt(backup.read_bytes(), aad=aad) != payload:
                raise MigrationError('existing recovery snapshot differs; no original was changed')
        else:
            encrypted = cipher.encrypt(payload, aad=aad)[0]
            if cipher.decrypt(encrypted, aad=aad) != payload:
                raise MigrationError('credential recovery verification failed')
            atomic_bytes(backup, encrypted)
        _import(vault, records, clients)
        changed = []
        for name, raw in pending.items():
            path = source / name
            if path.is_symlink() or path.read_bytes() != raw:
                raise MigrationError('legacy inputs changed before sealing; encrypted recovery was preserved')
            envelope = cipher.encrypt(raw, aad=('artha.credential.source:' + name).encode())[0]
            if cipher.decrypt(envelope, aad=('artha.credential.source:' + name).encode()) != raw:
                raise MigrationError('original sealing verification failed')
            atomic_bytes(path, envelope)
            changed.append(name)
        return {'scope': scope, 'records': sorted(records), 'sealed': changed, 'backup': str(backup)}


def _read_recovery(backup: Path, backend: Any, work: bool) -> tuple[str, dict[str, Any], dict[str, str]]:
    scope = 'work' if work else 'personal'
    match = re.fullmatch(scope + r'-([0-9a-f]{64})\.ae1', backup.name)
    if not match or backup.is_symlink() or backup.stat().st_size > 12_000_000:
        raise MigrationError('credential recovery path, scope or size is invalid')
    envelope = backup.read_bytes()
    cipher = EnvelopeCipher(KeyringKeyProvider(f'artha-credential-recovery-{scope}', backend=backend, create=False))
    payload = json.loads(cipher.decrypt(envelope,
        aad=f'artha.credentials.migration.v1:{scope}:{match[1]}'.encode()))
    if (not isinstance(payload, dict) or payload.get('schema') != 'artha.credentials.migration.v1'
            or payload.get('scope') != scope or not isinstance(payload.get('files'), dict)):
        raise MigrationError('credential recovery schema or scope is invalid')
    try:
        files = {name: base64.b64decode(raw, validate=True) for name, raw in payload['files'].items()}
    except (ValueError, TypeError):
        raise MigrationError('credential recovery file encoding is invalid') from None
    _, records, clients, sealed = _decode(files, work)
    if sealed:
        raise MigrationError('credential recovery does not contain original records')
    return hashlib.sha256(envelope).hexdigest(), records, clients


def recovery_plan(backup: Path, *, backend: Any = None, work: bool = False) -> dict[str, Any]:
    vault = CredentialStore(backend=backend)
    digest, records, clients = _read_recovery(backup, vault._backend, work)
    return {'scope': 'work' if work else 'personal', 'review_hash': digest,
            'records': sorted(records), 'client_credentials': sorted(clients)}


def recover(backup: Path, local: Path, review_hash: str, *, backend: Any = None,
            work: bool = False) -> dict[str, Any]:
    """Import a reviewed encrypted recovery into the OS store, never into plaintext files."""
    from catchup_core.session_lock import installation_fence, require_active_installation  # noqa: PLC0415
    require_active_installation(backup, local)
    scope = 'work' if work else 'personal'
    with (
        installation_fence(local),
        session_lock(local / 'credential-locks' / f'migration-{scope}.lock'),
    ):
        vault = CredentialStore(backend=backend)
        digest, records, clients = _read_recovery(backup, vault._backend, work)
        if digest != review_hash:
            raise MigrationError('credential recovery changed; review the new plan')
        _check_existing(vault, records, clients)
        _import(vault, records, clients)
        return {'scope': scope, 'records': sorted(records), 'client_credentials': sorted(clients)}
