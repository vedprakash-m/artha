"""Local Work recovery restrictions, not source permissions or a consent ledger.

Callers inject a trusted Windows machine/user identity resolver and a user/machine
protected native credential backend. Neither is discovered or enrolled implicitly.
The identity key, native head and this module's authority directory must NEVER enter
checkpoint members, escrow or sync. All historical prose still requires file review.

Publication uses cooperative process locks and exact predecessor comparison. Native
credential stores provide no compare-and-swap. An interrupted intent is fail-closed
until explicit roll-forward; it is never evidence that a replacement was rolled back.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from .backup import atomic_bytes
from .crypto import DecryptionError, EnvelopeCipher, StaticKeyProvider, system_keyring
from .session_lock import default_installation_dir, installation_fence, require_active_installation, session_lock

_MAX_MEMBERS = 10000
_MAX_MEMBER_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_BYTES = 64 * 1024 * 1024
_MAX_GENERATIONS = 10000
_HEX = re.compile(r'[0-9a-f]{64}')
_ALIAS = re.compile(r'[a-z][a-z0-9_]{0,63}')
_DEVICE = re.compile(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', re.IGNORECASE)


class WorkPrivacyBlocked(RuntimeError):
    """Local identity/currentness cannot be proven; no implicit adoption."""


def _encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkPrivacyBlocked('duplicate Work authority fields')
        result[key] = value
    return result


def _decoded(raw: bytes) -> dict[str, Any]:
    try:
        result = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise WorkPrivacyBlocked('malformed Work authority') from None
    if not isinstance(result, dict):
        raise WorkPrivacyBlocked('malformed Work authority')
    return result


def _chain(path: Path) -> None:
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        # Cloud file tags are not name-surrogate links; the native open still
        # verifies actual handles. Junctions and other reparse points refuse.
        cloud = getattr(info, 'st_reparse_tag', 0) & 0xFFFF0000 == 0x90000000
        if stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & 0x400 and not cloud):
            raise WorkPrivacyBlocked('linked Work protocol path')


def _read(path: Path, bound: int = _MAX_BYTES) -> bytes:
    _chain(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise WorkPrivacyBlocked('required local Work member or authority is missing') from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > bound:
        raise WorkPrivacyBlocked('unsafe or oversized Work protocol member')
    if os.name == 'nt':
        from .quarantine_fs_windows import WindowsQuarantineDirectory  # noqa: PLC0415

        with (WindowsQuarantineDirectory.open(path.parent, owner_private=False) as parent,
              parent.open_file(path.name, private=False) as handle):
            raw = handle.read(bound + 1)
            handle.check_path()
    else:
        descriptor = os.open(path, os.O_RDONLY | _posix_flag('O_NOFOLLOW') | _posix_flag('O_NONBLOCK'))
        with os.fdopen(descriptor, 'rb') as handle:
            before = os.fstat(handle.fileno())
            raw = handle.read(bound + 1)
            after = os.fstat(handle.fileno())
            current = path.lstat()
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
                raise WorkPrivacyBlocked('Work protocol member changed during read')
    if len(raw) != info.st_size or len(raw) > bound:
        raise WorkPrivacyBlocked('Work protocol member changed during read')
    return raw


def _mkdir(path: Path, *, private: bool = True) -> None:
    _chain(path)
    if os.name == 'nt':
        from .quarantine_fs_windows import WindowsQuarantineDirectory, ensure_private_directory  # noqa: PLC0415

        if path.exists() and not private:
            with WindowsQuarantineDirectory.open(path, owner_private=False):
                pass
        else:
            ensure_private_directory(path)
    else:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)


def _atomic(path: Path, raw: bytes) -> None:
    _chain(path)
    _mkdir(path.parent, private=False)
    if path.exists():
        _read(path)
    if os.name != 'nt':
        atomic_bytes(path, raw)
        return
    from .quarantine_fs_windows import WindowsQuarantineDirectory  # noqa: PLC0415

    class _PublicationDirectory(WindowsQuarantineDirectory):
        def open_file(self, name: str, *, writable: bool = False, rename: bool = False,
                      private: bool = True) -> Any:
            # Existing Work files predate private native publication. Pin their
            # identity without treating their inherited ACL as recovery authority.
            return super().open_file(name, writable=writable, rename=rename,
                                     private=private and name != path.name)

    with _PublicationDirectory.open(path.parent, owner_private=False, writable=True) as parent:
        temporary = '.work-' + uuid.uuid4().hex
        with parent.create_file(temporary) as staged:
            staged.write_all(raw)
            staged.flush()
            identity = staged.identity
        if path.exists():
            with parent.open_file(path.name, private=False) as previous:
                replaced = previous.identity
            parent.replace_file(temporary, path.name, expected_identity=identity, replaced_identity=replaced)
        else:
            parent.publish_write_once(temporary, path.name, expected_identity=identity)


def _delete(path: Path) -> None:
    _chain(path)
    if os.name == 'nt':
        from .quarantine_fs_windows import WindowsQuarantineDirectory  # noqa: PLC0415

        with WindowsQuarantineDirectory.open(path.parent, owner_private=False, writable=True) as parent:
            with parent.open_file(path.name, private=False) as handle:
                identity = handle.identity
            parent.delete_file(path.name, expected_identity=identity)
    else:
        descriptor = os.open(path.parent, os.O_RDONLY | _posix_flag('O_DIRECTORY') | _posix_flag('O_NOFOLLOW'))
        try:
            info = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise WorkPrivacyBlocked('unsafe Work member deletion')
            os.unlink(path.name, dir_fd=descriptor)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _posix_flag(name: str) -> int:
    value = getattr(os, name, None)
    if not isinstance(value, int):
        raise WorkPrivacyBlocked('secure Work filesystem operation is unavailable')
    return value


class _Transaction:
    def __init__(self, protocol: WorkPrivacy, pinned: dict[str, Any]):
        self.protocol = protocol
        self.pinned = pinned
        self.active = True
        self.published = False

    def check_current(self) -> dict[str, Any]:
        """Recheck pinned capture authority without reacquiring the transaction's locks."""
        if not self.active or self.published:
            raise WorkPrivacyBlocked('Work transaction is closed')
        current = self.protocol._load_current()
        if current != self.pinned:
            raise WorkPrivacyBlocked('Work transaction authority changed')
        return current

    def publish(self, changes: Mapping[str, bytes | None], *, revoke: Sequence[str] = ()) -> dict[str, Any]:
        if not self.active or self.published:
            raise WorkPrivacyBlocked('Work transaction is closed')
        self.published = True
        return self.protocol._publish(self.pinned, changes, revoke)


class WorkPrivacy:
    KEY_SERVICE = 'artha-work-local-identity'
    HEAD_SERVICE = 'artha-work-local-privacy'
    EXCLUDED_SERVICES = frozenset({KEY_SERVICE, HEAD_SERVICE})
    EPHEMERAL_MEMBER = 'work_local/cache/cache.lock'

    def __init__(self, repo: Path, local: Path, roots: Mapping[str, Path], *,
                 backend: Any, native_identity: Callable[[], Mapping[str, str]],
                 observe: Callable[[str], None] | None = None):
        self.repo = repo.absolute()
        self.local = local.absolute()
        self.roots = {name: path.absolute() for name, path in roots.items()}
        self.backend = backend
        self.native_identity = native_identity
        self.observe = observe or (lambda _: None)
        self.authority_dir = self.local / 'work-privacy'
        if not self.roots or any(not _ALIAS.fullmatch(name) for name in self.roots):
            raise WorkPrivacyBlocked('explicit Work member roots are required')
        paths = list(self.roots.values())
        if any(a.resolve().is_relative_to(b.resolve()) for i, a in enumerate(paths)
               for j, b in enumerate(paths) if i != j):
            raise WorkPrivacyBlocked('Work member roots overlap')
        if any(self.authority_dir.resolve().is_relative_to(path.resolve())
               or path.resolve().is_relative_to(self.authority_dir.resolve()) for path in paths):
            raise WorkPrivacyBlocked('local Work authority must be outside checkpoint member roots')

    def is_enrolled(self) -> bool:
        """Filesystem-only probe. Partial/corrupt enrollment must never enable legacy fallback."""
        _chain(self.authority_dir)
        return self.authority_dir.exists()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        require_active_installation(self.repo, self.local, *self.roots.values())
        with ExitStack() as stack:
            for path in sorted({self.local.resolve(), default_installation_dir().resolve()}):
                stack.enter_context(installation_fence(path))
            stack.enter_context(session_lock(default_installation_dir() / 'work-privacy.lock'))
            yield

    def _configuration(self) -> dict[str, Any]:
        native = dict(self.native_identity())
        if (set(native) != {'platform', 'machine', 'user'} or native['platform'] != 'win32'
                or any(not isinstance(value, str) or not value.strip() or len(value) > 1024
                       for value in native.values())):
            raise WorkPrivacyBlocked('trusted Windows machine and user identity required')
        for path in (self.repo, self.local, *self.roots.values(), self.authority_dir):
            _chain(path)
        def canonical(path: Path) -> str:
            return os.path.normcase(str(path.resolve()))

        return {'native': native, 'repo': canonical(self.repo), 'local': canonical(self.local),
                'roots': {name: canonical(path) for name, path in self.roots.items()}}

    def _key(self) -> bytes:
        raw = self.backend.get_password(self.KEY_SERVICE, 'key')
        if not isinstance(raw, str):
            raise WorkPrivacyBlocked('installed local Work identity key required')
        try:
            key = base64.b64decode(raw, validate=True)
        except ValueError:
            raise WorkPrivacyBlocked('invalid local Work identity key') from None
        if len(key) != 32:
            raise WorkPrivacyBlocked('invalid local Work identity key')
        return key

    def _cipher(self) -> EnvelopeCipher:
        key = hmac.digest(self._key(), b'artha.work-local.encryption.v1', 'sha256')
        return EnvelopeCipher(StaticKeyProvider({1: key}))

    def _encrypt(self, value: Mapping[str, Any], purpose: str) -> bytes:
        raw = _encoded(value)
        if len(raw) > _MAX_BYTES:
            raise WorkPrivacyBlocked('Work authority exceeds its supported bound')
        return self._cipher().encrypt(raw, aad=('artha.work-local:' + purpose).encode())[0]

    def _decrypt(self, raw: bytes, purpose: str) -> dict[str, Any]:
        try:
            plain = self._cipher().decrypt(raw, aad=('artha.work-local:' + purpose).encode())
        except DecryptionError:
            raise WorkPrivacyBlocked('local Work identity/key or ciphertext mismatch') from None
        if len(plain) > _MAX_BYTES:
            raise WorkPrivacyBlocked('Work authority exceeds its supported bound')
        return _decoded(plain)

    def _sign(self, body: Mapping[str, Any], purpose: str) -> dict[str, Any]:
        return {**body, 'mac': hmac.digest(self._key(), purpose.encode() + b'\0' + _encoded(body),
                                         'sha256').hex()}

    def _verify(self, signed: Mapping[str, Any], purpose: str) -> dict[str, Any]:
        body = {key: value for key, value in signed.items() if key != 'mac'}
        mac = signed.get('mac')
        if not isinstance(mac, str) or not hmac.compare_digest(self._sign(body, purpose)['mac'], mac):
            raise WorkPrivacyBlocked('Work authority authentication failed')
        return body

    def _identity(self) -> str:
        if not self.is_enrolled():
            raise WorkPrivacyBlocked('explicit reviewed Work enrollment is required')
        identity = self._decrypt(_read(self.authority_dir / 'identity'), 'identity')
        if (set(identity) != {'schema', 'installation_id', 'configuration'}
                or identity['schema'] != 'artha.work-installation.v1'
                or not isinstance(identity['installation_id'], str) or not identity['installation_id']
                or identity['configuration'] != self._configuration()):
            raise WorkPrivacyBlocked('original Work host/profile/installation identity mismatch')
        return _digest(identity)

    def _member(self, name: str) -> Path:
        if not isinstance(name, str) or len(name) > 1024:
            raise WorkPrivacyBlocked('invalid Work member name')
        parts = name.split('/')
        if len(parts) < 2 or parts[0] not in self.roots:
            raise WorkPrivacyBlocked('unknown Work member root')
        if any(not part or part in {'.', '..'} or part.endswith((' ', '.'))
               or _DEVICE.fullmatch(part) or any(char in '<>:"\\|?*' or ord(char) < 32 for char in part)
               for part in parts[1:]):
            raise WorkPrivacyBlocked('unsafe Work member name')
        path = self.roots[parts[0]].joinpath(*parts[1:])
        _chain(path)
        return path

    def _inventory(self) -> dict[str, Any]:
        members: dict[str, Any] = {}
        folded: set[str] = set()
        total = 0
        for alias, root in sorted(self.roots.items()):
            _chain(root)
            if not root.is_dir():
                raise WorkPrivacyBlocked('Work member root is missing')
            def unavailable(error: OSError) -> None:
                raise error

            for directory, directories, files in os.walk(root, followlinks=False, onerror=unavailable):
                for name in directories:
                    _chain(Path(directory) / name)
                for name in files:
                    path = Path(directory) / name
                    member = alias + '/' + path.relative_to(root).as_posix()
                    self._member(member)
                    if member == self.EPHEMERAL_MEMBER:
                        info = path.lstat()
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise WorkPrivacyBlocked('unsafe ephemeral Work cache lock')
                        continue
                    if member.casefold() in folded or len(members) >= _MAX_MEMBERS:
                        raise WorkPrivacyBlocked('ambiguous or oversized Work inventory')
                    raw = _read(path, _MAX_MEMBER_BYTES)
                    total += len(raw)
                    if total > _MAX_TOTAL_BYTES:
                        raise WorkPrivacyBlocked('Work inventory exceeds its byte bound')
                    folded.add(member.casefold())
                    members[member] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        return members

    def _history(self, head: Mapping[str, Any]) -> dict[str, Any]:
        body = self._verify(head, 'head')
        if (set(body) != {'schema', 'identity', 'generation', 'capsule_sha256', 'previous'}
                or body['schema'] != 'artha.work-privacy-head.v1'
                or body['identity'] != self._identity()
                or not isinstance(body['capsule_sha256'], str) or not _HEX.fullmatch(body['capsule_sha256'])):
            raise WorkPrivacyBlocked('invalid current Work head')
        raw = _read(self.authority_dir / (body['capsule_sha256'] + '.ae1'))
        if hashlib.sha256(raw).hexdigest() != body['capsule_sha256']:
            raise WorkPrivacyBlocked('Work capsule differs from current trusted head')
        history = self._decrypt(raw, 'history')
        self._validate_history(history)
        if (history['generation'], history['identity']) != (body['generation'], body['identity']):
            raise WorkPrivacyBlocked('Work history differs from current trusted head')
        return history

    def _validate_history(self, history: dict[str, Any]) -> None:
        if (set(history) != {'schema', 'profile', 'identity', 'generation', 'members', 'invalidated',
                             'revoked', 'events'}
                or history['schema'] != 'artha.work-member-history.v1' or history['profile'] != 'work'
                or type(history['generation']) is not int or not 1 <= history['generation'] <= _MAX_GENERATIONS
                or not isinstance(history['members'], dict) or len(history['members']) > _MAX_MEMBERS
                or not isinstance(history['invalidated'], dict) or len(history['invalidated']) > _MAX_MEMBERS
                or not isinstance(history['revoked'], list) or len(history['revoked']) > _MAX_MEMBERS
                or len(set(history['revoked'])) != len(history['revoked'])
                or not isinstance(history['events'], list) or len(history['events']) != history['generation']):
            raise WorkPrivacyBlocked('invalid Work member history')
        for name, value in history['members'].items():
            self._member(name)
            if (not isinstance(value, dict) or set(value) != {'sha256', 'size'}
                    or not isinstance(value['sha256'], str) or not _HEX.fullmatch(value['sha256'])
                    or type(value['size']) is not int or not 0 <= value['size'] <= _MAX_MEMBER_BYTES):
                raise WorkPrivacyBlocked('invalid Work member digest')
        for name, generation in history['invalidated'].items():
            self._member(name)
            if type(generation) is not int or not 1 <= generation <= history['generation']:
                raise WorkPrivacyBlocked('invalid Work invalidation')
        for name in history['revoked']:
            self._member(name)
        for generation, event in enumerate(history['events'], 1):
            if (not isinstance(event, dict) or set(event) != {
                    'generation', 'members_sha256', 'previous_head_sha256'}
                    or event['generation'] != generation
                    or not isinstance(event['members_sha256'], str) or not _HEX.fullmatch(event['members_sha256'])
                    or (generation == 1 and event['previous_head_sha256'] is not None)
                    or (generation > 1 and (not isinstance(event['previous_head_sha256'], str)
                                           or not _HEX.fullmatch(event['previous_head_sha256'])))):
                raise WorkPrivacyBlocked('invalid Work history continuity')
        if history['events'][-1]['members_sha256'] != _digest(history['members']):
            raise WorkPrivacyBlocked('Work member inventory differs from history')

    def _native_head(self) -> dict[str, Any]:
        raw = self.backend.get_password(self.HEAD_SERVICE, 'head')
        if not isinstance(raw, str) or len(raw) > 65536:
            raise WorkPrivacyBlocked('current native Work head is unavailable')
        return _decoded(raw.encode())

    def _expect_head(self, expected: Mapping[str, Any]) -> None:
        if self._native_head() != expected:
            raise WorkPrivacyBlocked('current Work head changed; publication cannot succeed')
        self._identity()

    def _load_current(self, *, drift: bool = True) -> dict[str, Any]:
        self._identity()
        if (self.authority_dir / 'pending').exists():
            raise WorkPrivacyBlocked('pending Work publication requires explicit recovery')
        head = self._native_head()
        history = self._history(head)
        complete = self._decrypt(_read(self.authority_dir / 'complete'), 'complete')
        if complete != {'head': head}:
            raise WorkPrivacyBlocked('Work head differs from acknowledged history; rollback or interrupted publication')
        if drift and self._inventory() != history['members']:
            raise WorkPrivacyBlocked('unjournaled Work member drift requires reviewed reconciliation')
        self._expect_head(head)
        return {**history, 'head': head}

    def current(self) -> dict[str, Any]:
        with self._locked():
            return self._load_current()

    def enroll(self, identity_key: bytes, *, reviewed: bool, installation_id: str | None = None) -> dict[str, Any]:
        if reviewed is not True:
            raise WorkPrivacyBlocked('explicit inventory review required before Work enrollment')
        if not isinstance(identity_key, bytes) or len(identity_key) != 32:
            raise WorkPrivacyBlocked('explicit local identity key must contain 32 bytes')
        with self._locked():
            if (self.is_enrolled() or self.backend.get_password(self.KEY_SERVICE, 'key') is not None
                    or self.backend.get_password(self.HEAD_SERVICE, 'head') is not None):
                raise WorkPrivacyBlocked('Work identity is already enrolled or has incomplete enrollment')
            identity = {'schema': 'artha.work-installation.v1',
                        'installation_id': installation_id or uuid.uuid4().hex,
                        'configuration': self._configuration()}
            if not isinstance(identity['installation_id'], str) or not 1 <= len(identity['installation_id']) <= 128:
                raise WorkPrivacyBlocked('invalid explicit installation identity')
            members = self._inventory()
            _mkdir(self.authority_dir)
            text = base64.b64encode(identity_key).decode()
            self.backend.set_password(self.KEY_SERVICE, 'key', text)
            if self._key() != identity_key:
                raise WorkPrivacyBlocked('local identity key publication could not be verified')
            _atomic(self.authority_dir / 'identity', self._encrypt(identity, 'identity'))
            history = {'schema': 'artha.work-member-history.v1', 'profile': 'work',
                       'identity': _digest(identity), 'generation': 1, 'members': members,
                       'invalidated': {}, 'revoked': [], 'events': [
                           {'generation': 1, 'members_sha256': _digest(members), 'previous_head_sha256': None}]}
            head, raw = self._capsule(history, None)
            _atomic(self.authority_dir / (head['capsule_sha256'] + '.ae1'), raw)
            if self._inventory() != members or self._identity() != history['identity']:
                raise WorkPrivacyBlocked('Work identity/inventory changed during enrollment')
            self.backend.set_password(self.HEAD_SERVICE, 'head', _encoded(head).decode())
            self._expect_head(head)
            _atomic(self.authority_dir / 'complete', self._encrypt({'head': head}, 'complete'))
            return self._load_current()

    def enroll_generated(self, *, reviewed: bool, installation_id: str | None = None) -> dict[str, Any]:
        """Explicit native enrollment; callers must separately obtain live-operation authorization."""
        if reviewed is not True:
            raise WorkPrivacyBlocked('explicit inventory review required before Work enrollment')
        return self.enroll(secrets.token_bytes(32), reviewed=True, installation_id=installation_id)

    def _capsule(self, history: dict[str, Any], previous: Mapping[str, Any] | None) -> tuple[dict[str, Any], bytes]:
        self._validate_history(history)
        raw = self._encrypt(history, 'history')
        head = self._sign({'schema': 'artha.work-privacy-head.v1', 'identity': history['identity'],
                           'generation': history['generation'], 'capsule_sha256': hashlib.sha256(raw).hexdigest(),
                           'previous': _digest(previous) if previous is not None else None}, 'head')
        return head, raw

    @contextmanager
    def transaction(self) -> Iterator[_Transaction]:
        with self._locked():
            transaction = _Transaction(self, self._load_current())
            try:
                yield transaction
            finally:
                transaction.active = False

    def publish(self, changes: Mapping[str, bytes | None], *, revoke: Sequence[str] = ()) -> dict[str, Any]:
        with self.transaction() as transaction:
            return transaction.publish(changes, revoke=revoke)

    @staticmethod
    def _continuity(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
        if (after['generation'] != before['generation'] + 1 or before['identity'] != after['identity']
                or after['events'][:-1] != before['events']
                or not set(before['revoked']) <= set(after['revoked'])
                or any(after['invalidated'].get(name, 0) < version for name, version in before['invalidated'].items())):
            raise WorkPrivacyBlocked('Work history cannot roll back invalidations or identity')

    def _prepare(self, before: dict[str, Any], members: dict[str, Any], changes: Mapping[str, bytes | None],
                 revoke: Sequence[str]) -> dict[str, Any]:
        generation = before['generation'] + 1
        invalidated = dict(before['invalidated'])
        changed = {name for name in set(before['members']) | set(members)
                   if before['members'].get(name) != members.get(name)}
        invalidated.update(dict.fromkeys(changed, generation))
        after = {key: value for key, value in before.items() if key != 'head'}
        after.update(generation=generation, members=members, invalidated=invalidated,
                     revoked=sorted(set(before['revoked']) | set(revoke)),
                     events=[*before['events'], {'generation': generation, 'members_sha256': _digest(members),
                                                 'previous_head_sha256': _digest(before['head'])}])
        self._validate_history(after)
        self._continuity(before, after)
        head, capsule = self._capsule(after, before['head'])
        return {'schema': 'artha.work-pending.v1', 'before': before, 'after': after, 'head': head,
                'capsule': base64.b64encode(capsule).decode(),
                'changes': {name: None if value is None else base64.b64encode(value).decode()
                            for name, value in changes.items()}}

    def _publish(self, before: dict[str, Any], changes: Mapping[str, bytes | None],
                 revoke: Sequence[str]) -> dict[str, Any]:
        if self._load_current() != before:
            raise WorkPrivacyBlocked('Work transaction changed before publication')
        members = dict(before['members'])
        folded = {name.casefold(): name for name in members}
        for name, raw in changes.items():
            self._member(name)
            if name == self.EPHEMERAL_MEMBER:
                raise WorkPrivacyBlocked('ephemeral Work cache lock cannot become a journal member')
            if name.casefold() in folded and folded[name.casefold()] != name:
                raise WorkPrivacyBlocked('ambiguous Work member name')
            folded[name.casefold()] = name
            if raw is None:
                members.pop(name, None)
            elif isinstance(raw, bytes) and len(raw) <= _MAX_MEMBER_BYTES:
                members[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
            else:
                raise WorkPrivacyBlocked('invalid or oversized staged Work member')
        for name in revoke:
            self._member(name)
        if len(members) > _MAX_MEMBERS or sum(value['size'] for value in members.values()) > _MAX_TOTAL_BYTES:
            raise WorkPrivacyBlocked('Work inventory exceeds its supported bound')
        if members == before['members'] and set(revoke) <= set(before['revoked']):
            current = self._load_current()
            if current != before:
                raise WorkPrivacyBlocked('Work transaction changed before no-op acknowledgement')
            return current
        pending = self._prepare(before, members, changes, revoke)
        _atomic(self.authority_dir / 'pending', self._encrypt(pending, 'pending'))
        self.observe('intent')
        return self._finish(pending)

    def _finish(self, pending: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0912 - ordered crash-safe publication
        before, after, head = pending['before'], pending['after'], pending['head']
        self._continuity(before, after)
        self._validate_history(after)
        self._history(before['head'])
        capsule = base64.b64decode(pending['capsule'], validate=True)
        if (head != self._sign(self._verify(head, 'head'), 'head')
                or hashlib.sha256(capsule).hexdigest() != head.get('capsule_sha256')
                or self._decrypt(capsule, 'history') != after
                or head.get('previous') != _digest(before['head'])
                or after['events'][-1]['previous_head_sha256'] != _digest(before['head'])):
            raise WorkPrivacyBlocked('pending Work authority differs from authenticated history')
        native = self._native_head()
        if native not in (before['head'], head):
            raise WorkPrivacyBlocked('current Work head changed during pending publication')
        complete = self._decrypt(_read(self.authority_dir / 'complete'), 'complete')
        if complete not in ({'head': before['head']}, {'head': head}):
            raise WorkPrivacyBlocked('pending Work completion identity changed')
        current = self._inventory()
        for name in set(current) | set(before['members']) | set(after['members']):
            if current.get(name) not in (before['members'].get(name), after['members'].get(name)):
                raise WorkPrivacyBlocked('unjournaled Work drift during pending publication')
        _atomic(self.authority_dir / (head['capsule_sha256'] + '.ae1'), capsule)
        self.observe('capsule')
        for name, value in pending['changes'].items():
            path = self._member(name)
            self._expect_head(native)
            if value is None:
                if path.exists():
                    _read(path, _MAX_MEMBER_BYTES)
                    _delete(path)
            else:
                raw = base64.b64decode(value, validate=True)
                if {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)} != after['members'].get(name):
                    raise WorkPrivacyBlocked('staged Work bytes differ from intent')
                _atomic(path, raw)
            self.observe('member')
        if self._inventory() != after['members']:
            raise WorkPrivacyBlocked('Work inventory drift at publication')
        self._expect_head(native)
        if native == before['head']:
            self.backend.set_password(self.HEAD_SERVICE, 'head', _encoded(head).decode())
        self.observe('head')
        self._expect_head(head)
        self.observe('readback')
        self._expect_head(head)
        _atomic(self.authority_dir / 'complete', self._encrypt({'head': head}, 'complete'))
        self.observe('complete')
        self._expect_head(head)
        if self._inventory() != after['members']:
            raise WorkPrivacyBlocked('Work inventory drift after publication')
        _delete(self.authority_dir / 'pending')
        return self._load_current()

    def recover_pending(self) -> dict[str, Any]:
        """Explicit deterministic roll-forward only; never adopt unrelated live edits."""
        with self._locked():
            self._identity()
            pending = self._decrypt(_read(self.authority_dir / 'pending'), 'pending')
            if pending.get('schema') != 'artha.work-pending.v1':
                raise WorkPrivacyBlocked('invalid Work pending intent')
            return self._finish(pending)

    def reconcile(self, *, reviewed: bool) -> dict[str, Any]:
        if reviewed is not True:
            raise WorkPrivacyBlocked('explicit review required for Work drift reconciliation')
        with self._locked():
            before = self._load_current(drift=False)
            members = self._inventory()
            pending = self._prepare(before, members, {}, ())
            _atomic(self.authority_dir / 'pending', self._encrypt(pending, 'pending'))
            self.observe('intent')
            return self._finish(pending)

    def bind_checkpoint(self) -> dict[str, Any]:
        """Authenticated provenance only. Every surviving member still needs human review."""
        with self._locked():
            current = self._load_current()
            return self._sign({'schema': 'artha.work-checkpoint-binding.v1', 'profile': 'work',
                               'identity': current['identity'], 'generation': current['generation'],
                               'head_sha256': _digest(current['head']), 'members': current['members'],
                               'plaintext_allowed': False}, 'checkpoint')

    def _check_checkpoint(self, binding: Mapping[str, Any], current: dict[str, Any]) -> None:
        body = self._verify(binding, 'checkpoint')
        if (set(body) != {'schema', 'profile', 'identity', 'generation', 'head_sha256', 'members',
                          'plaintext_allowed'}
                or body['schema'] != 'artha.work-checkpoint-binding.v1' or body['profile'] != 'work'
                or body['identity'] != current['identity'] or body['plaintext_allowed'] is not False
                or type(body['generation']) is not int or not 1 <= body['generation'] <= current['generation']):
            raise WorkPrivacyBlocked('checkpoint lacks original Work installation binding')
        generation = body['generation']
        event = current['events'][generation - 1]
        head = (_digest(current['head']) if generation == current['generation']
                else current['events'][generation]['previous_head_sha256'])
        if event['members_sha256'] != _digest(body['members']) or body['head_sha256'] != head:
            raise WorkPrivacyBlocked('checkpoint history is not retained in current Work authority')

    def check_checkpoint(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        with self._locked():
            current = self._load_current()
            self._check_checkpoint(binding, current)
            return current

    def denied_members(self, binding: Mapping[str, Any], members: Sequence[str]) -> set[str]:
        with self._locked():
            current = self._load_current()
            self._check_checkpoint(binding, current)
            for name in members:
                self._member(name)
            return {name for name in members if name not in binding['members']
                    or name in current['revoked']
                    or current['invalidated'].get(name, 0) > binding['generation']}


class _DeferredNativeBackend:
    def __init__(self) -> None:
        self._backend: Any = None

    def _native(self) -> Any:
        if self._backend is None:
            self._backend = system_keyring()
        return self._backend

    def get_password(self, service: str, account: str) -> str | None:
        value = self._native().get_password(service, account)
        if value is not None and not isinstance(value, str):
            raise WorkPrivacyBlocked('native Work credential record is malformed')
        return value

    def set_password(self, service: str, account: str, value: str) -> None:
        self._native().set_password(service, account, value)


def native_windows_identity() -> dict[str, str]:
    """Resolve native identity only when explicitly invoked; never use a hostname fallback."""
    if sys.platform != 'win32':
        raise WorkPrivacyBlocked('local Work recovery requires the original Windows installation')
    import winreg  # noqa: PLC0415

    from .quarantine_fs_windows import _native  # noqa: PLC0415

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        value, kind = winreg.QueryValueEx(key, 'MachineGuid')
    if kind != winreg.REG_SZ or not isinstance(value, str):
        raise WorkPrivacyBlocked('native Windows machine identity is unavailable')
    try:
        machine = str(uuid.UUID(value))
    except ValueError:
        raise WorkPrivacyBlocked('native Windows machine identity is invalid') from None
    user, _ = _native().current_user_sid()
    if not isinstance(user, str) or not re.fullmatch(r'S-[0-9]+(?:-[0-9]+)+', user):
        raise WorkPrivacyBlocked('native Windows user identity is invalid')
    return {'platform': 'win32', 'machine': machine, 'user': user}


def for_installation(repo: Path, local: Path | None = None, *, backend: Any = None,
                     native_identity: Callable[[], Mapping[str, str]] | None = None,
                     observe: Callable[[str], None] | None = None) -> WorkPrivacy:
    """Canonical Work roots only; outside-root credentials remain checkpoint dependencies."""
    local = default_installation_dir() if local is None else local
    return WorkPrivacy(repo, local, {'work_state': repo / 'state/work', 'work_local': local / 'work'},
                       backend=_DeferredNativeBackend() if backend is None else backend,
                       native_identity=native_windows_identity if native_identity is None else native_identity,
                       observe=observe)
