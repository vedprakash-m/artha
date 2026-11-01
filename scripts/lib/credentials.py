"""Platform credential-store records; no secret file or synced-file fallback.

Small ASCII chunks keep each OS credential value bounded. A verified head publishes
one immutable generation; a preceding generation remains until the next update so
an in-flight reader can finish. Revocation publishes a tombstone before cleanup.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from catchup_core.session_lock import SessionBusy, installation_fence, session_lock
from catchup_core.crypto import KeyUnavailableError, system_keyring as platform_keyring

SERVICE = 'artha.credentials.v1'
WORK_SERVICE = 'artha.credentials.work.v1'
_SCHEMA = 'artha.credential.v1'
_CHUNK = 1000
_MAX_CHUNKS = 64
_NAME = re.compile(r'[a-z][a-z0-9_.-]{0,63}')
_HEX = re.compile(r'[0-9a-f]{32}')


class CredentialError(RuntimeError):
    pass


def system_keyring() -> Any:
    """Only a platform credential backend may receive live secrets; file/null fallbacks are refused."""
    try:
        return platform_keyring()
    except KeyUnavailableError as exc:
        raise CredentialError(str(exc)) from None


class CredentialStore:
    def __init__(self, *, backend: Any = None, local: Path | None = None):
        if backend is None:
            backend = system_keyring()
        self._backend = backend
        # An OS service is shared across runtime roots. ``local`` is an explicit test injection.
        self._local = local or Path.home() / '.artha-local'

    def _lock_path(self, name: str, *, refresh: bool = False) -> Path:
        identity = self._service(name) + ':' + name
        return self._local / ('credential-refresh-locks' if refresh else 'credential-locks') / (
            hashlib.sha256(identity.encode()).hexdigest() + '.lock')

    @contextmanager
    def refresh_lock(self, name: str) -> Iterator[None]:
        """Serialize a read/refresh/publication sequence without blocking explicit login/revocation."""
        self._name(name)
        self._check_writable()
        try:
            with installation_fence(self._local), session_lock(self._lock_path(name, refresh=True)):
                yield
        except (SessionBusy, OSError):
            raise CredentialError('credential refresh unavailable; retry after the active refresh finishes') from None

    @contextmanager
    def _publication_lock(self, name: str) -> Iterator[None]:
        self._check_writable()
        try:
            with installation_fence(self._local), session_lock(self._lock_path(name)):
                yield
        except (SessionBusy, OSError):
            raise CredentialError('credential update unavailable; retry after the active update finishes') from None

    def _check_writable(self) -> None:
        from catchup_core import crypto
        if crypto._action_keyring is not None and self._backend is crypto._action_keyring:
            raise CredentialError('action credentials are read-only')

    @staticmethod
    def _service(account: str) -> str:
        record = account.split('.chunk.', 1)[0].removesuffix('.pending')
        return WORK_SERVICE if record in {'msgraph.work', 'msgraph.work-mcp'} or record.startswith('work.') else SERVICE

    def _get(self, account: str) -> str | None:
        try:
            value = self._backend.get_password(self._service(account), account)
        except Exception:
            raise CredentialError('OS credential store is unavailable') from None
        if value is not None and not isinstance(value, str):
            raise CredentialError('OS credential record has an invalid type')
        return value

    def _set(self, account: str, value: str) -> None:
        try:
            self._backend.set_password(self._service(account), account, value)
        except Exception:
            raise CredentialError('OS credential write could not be confirmed') from None
        if self._get(account) != value:
            raise CredentialError('OS credential store did not persist the record')

    def _remove(self, account: str) -> None:
        if self._get(account) is None:
            return
        try:
            self._backend.delete_password(self._service(account), account)
        except Exception:
            raise CredentialError('OS credential cleanup is incomplete') from None
        if self._get(account) is not None:
            raise CredentialError('OS credential cleanup could not be confirmed')

    def _name(self, name: str) -> None:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise CredentialError('credential name is invalid')

    def _head(self, name: str) -> dict[str, Any] | None:
        raw = self._get(name)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get('schema') != _SCHEMA:
                raise ValueError('unknown schema')
            refs = data.get('retired', [])
            if not isinstance(refs, list) or len(refs) > (2 if data.get('deleted') is True else 1):
                raise ValueError('invalid retired generation')
            for ref in refs + ([] if data.get('deleted') is True else [data]):
                if (not isinstance(ref, dict) or not isinstance(ref.get('generation'), str)
                        or not _HEX.fullmatch(ref['generation']) or type(ref.get('chunks')) is not int
                        or not 1 <= ref['chunks'] <= _MAX_CHUNKS):
                    raise ValueError('invalid generation')
            if data.get('deleted') is not True and (
                    not isinstance(data.get('digest'), str) or not re.fullmatch(r'[0-9a-f]{64}', data['digest'])):
                raise ValueError('invalid digest')
            return dict(data)
        except (ValueError, TypeError) as exc:
            raise CredentialError('OS credential record is corrupt; explicit recovery is required') from exc

    @staticmethod
    def _account(name: str, generation: str, index: int) -> str:
        return f'{name}.chunk.{generation}.{index}'

    def _clean(self, name: str, refs: list[dict[str, Any]]) -> None:
        for ref in refs:
            for index in range(ref['chunks']):
                self._remove(self._account(name, ref['generation'], index))

    def _recover_pending(self, name: str, current: dict[str, Any] | None) -> None:
        pending = self._head(name + '.pending')
        if pending is None:
            return
        if current is None or pending['generation'] != current.get('generation'):
            self._clean(name, [pending])
        self._remove(name + '.pending')

    def read(self, name: str) -> dict[str, Any] | None:
        return self.read_versioned(name)[0]

    @staticmethod
    def _revision(head: dict[str, Any] | None) -> str:
        return hashlib.sha256(json.dumps(head, sort_keys=True).encode()).hexdigest() if head is not None else ''

    def read_versioned(self, name: str) -> tuple[dict[str, Any] | None, str]:
        """Return the exact publication revision to bind a later silent refresh result."""
        self._name(name)
        for _ in range(2):  # a concurrent writer may have retired the generation just read
            head = self._head(name)
            if head is None or head.get('deleted') is True:
                return None, self._revision(head)
            chunks = [self._get(self._account(name, head['generation'], i)) for i in range(head['chunks'])]
            if any(chunk is None for chunk in chunks):
                if self._head(name) != head:
                    continue
                raise CredentialError('OS credential generation is incomplete')
            text = ''.join(chunk for chunk in chunks if chunk is not None)
            if not text.isascii() or hashlib.sha256(text.encode('ascii')).hexdigest() != head['digest']:
                raise CredentialError('OS credential generation failed integrity verification')
            try:
                payload = json.loads(text)
            except ValueError as exc:
                raise CredentialError('OS credential payload is corrupt') from exc
            if not isinstance(payload, dict):
                raise CredentialError('OS credential payload is not a record')
            return dict(payload), self._revision(head)
        raise CredentialError('OS credential generation changed; retry the read')

    def write(self, name: str, payload: dict[str, Any], *, replace_existing: bool = True,
              expected_revision: str | None = None) -> None:
        from catchup_core.recovery_registry import has_pending, mutation, profile_for

        self._name(name)
        if expected_revision is not None and (not isinstance(expected_revision, str)
                or expected_revision != '' and not re.fullmatch('[a-f0-9]{64}', expected_revision)):
            raise CredentialError('credential revision is invalid')
        try:
            text = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise CredentialError('credential payload is invalid') from exc
        if not isinstance(payload, dict) or len(text) > _MAX_CHUNKS * _CHUNK:
            raise CredentialError('credential payload is not a bounded record')
        with self._publication_lock(name):
            previous = self._head(name)
            if expected_revision is not None and self._revision(previous) != expected_revision:
                raise CredentialError('credential changed during refresh; the stale result was not published')
            if not replace_existing:
                current = self.read(name)
                if current is not None:
                    if current != payload:
                        raise CredentialError('OS store has a different credential; it was not overwritten')
                    service = self._service(name)
                    if previous is not None and has_pending(
                            profile_for(service, name), 'credential', service, name,
                            previous['generation'], root=self._local):
                        with mutation(profile_for(service, name), 'credential', service, name,
                                      previous['generation'], root=self._local):
                            verified, revision = self.read_versioned(name)
                            if revision != self._revision(previous) or verified != payload:
                                raise CredentialError('OS credential changed; pending publication was not completed')
                    return
            generation = uuid.uuid4().hex
            service = self._service(name)
            with mutation(profile_for(service, name), 'credential', service, name, generation, root=self._local):
                self._recover_pending(name, previous)
                self._clean(name, previous.get('retired', []) if previous else [])
                chunks = [text[i:i + _CHUNK] for i in range(0, len(text), _CHUNK)]
                manifest = {'schema': _SCHEMA, 'generation': generation, 'chunks': len(chunks),
                            'digest': hashlib.sha256(text.encode()).hexdigest(), 'retired': []}
                self._set(name + '.pending', json.dumps(manifest))
                for index, chunk in enumerate(chunks):
                    self._set(self._account(name, generation, index), chunk)
                retired = ([{'generation': previous['generation'], 'chunks': previous['chunks']}]
                           if previous and previous.get('deleted') is not True else [])
                self._set(name, json.dumps({**manifest, 'retired': retired}))
                self._remove(name + '.pending')

    def delete(self, name: str) -> None:
        from catchup_core.recovery_registry import mutation, profile_for

        self._name(name)
        with self._publication_lock(name):
            head = self._head(name)
            service = self._service(name)
            with mutation(profile_for(service, name), 'credential', service, name, uuid.uuid4().hex,
                          root=self._local, deleted=True):
                if head is None:
                    self._recover_pending(name, None)
                    return
                retired = head.get('retired', []) + ([] if head.get('deleted') is True else
                                                    [{'generation': head['generation'], 'chunks': head['chunks']}])
                self._set(name, json.dumps({'schema': _SCHEMA, 'deleted': True, 'retired': retired}))
                self._recover_pending(name, None)
                self._clean(name, retired)
                self._set(name, json.dumps({'schema': _SCHEMA, 'deleted': True, 'retired': []}))
