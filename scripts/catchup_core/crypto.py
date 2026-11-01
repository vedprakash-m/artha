"""Field encryption for restricted ledger values (re-artha §7.7, §10.6, D-04/D-20).

Envelope format (versioned, self-describing)::

    b"AE1" | key_version (4 bytes, big endian) | nonce (12) | AES-256-GCM ciphertext+tag

The data key lives in the OS credential store (``KeyringKeyProvider``); every
operation is in memory — no temporary file ever holds plaintext.  A missing key
never falls back to plaintext: :class:`KeyUnavailableError` propagates to the
caller, which blocks the affected operation (§6.13).  Associated data binds a
ciphertext to its record so a value cannot be moved to another row.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .session_lock import SessionBusy, default_installation_dir, installation_fence, session_lock

__all__ = [
    "DecryptionError", "EnvelopeCipher", "KeyProvider", "KeyUnavailableError",
    "KeyringKeyProvider", "StaticKeyProvider",
    "system_keyring",
]

_MAGIC = b"AE1"
_NONCE_BYTES = 12
_KEY_BYTES = 32
_MAX_VERSION = (1 << 32) - 1
# Installed only by the trusted action worker, never selected by configuration/backend priority.
_action_keyring: Any = None


class KeyUnavailableError(RuntimeError):
    """The data key cannot be obtained.  Callers block; they never write plaintext."""


class DecryptionError(RuntimeError):
    """Ciphertext failed authentication (tampered, wrong key, wrong record)."""


def system_keyring() -> Any:
    """Select a platform backend without file, null, or chained fallback reads."""
    if _action_keyring is not None:
        return _action_keyring
    allowed = {'keyring.backends.macos', 'keyring.backends.windows', 'keyring.backends.secretservice',
               'keyring.backends.libsecret', 'keyring.backends.kwallet'}
    try:
        import keyring  # noqa: PLC0415 - lazy discovery; explicit test backends need no OS store
        backend: Any = keyring.get_keyring()
        candidates = (backend.backends if type(backend).__module__ == 'keyring.backends.chainer' else [backend])
        for candidate in candidates:
            if type(candidate).__module__.lower() in allowed:
                return candidate
    except Exception:
        raise KeyUnavailableError('OS credential-store discovery is unavailable') from None
    raise KeyUnavailableError('An OS credential-store backend is required; file and null backends are not accepted')


class KeyProvider(Protocol):
    def current_version(self) -> int: ...

    def key(self, version: int) -> bytes: ...


class StaticKeyProvider:
    """In-memory keys (tests, and the unlocked-vault session in WP-06)."""

    def __init__(self, keys: dict[int, bytes], current: int | None = None):
        if not keys:
            raise ValueError("at least one key is required")
        for material in keys.values():
            if len(material) != _KEY_BYTES:
                raise ValueError("data keys are 32 bytes")
        self._keys = dict(keys)
        self._current = current if current is not None else max(keys)
        if self._current not in self._keys:
            raise ValueError("current key version not present")

    def current_version(self) -> int:
        return self._current

    def key(self, version: int) -> bytes:
        try:
            return self._keys[version]
        except KeyError as exc:
            raise KeyUnavailableError(f"no data key for version {version}") from exc


class KeyringKeyProvider:
    """Data keys held in the OS credential store.  Creates version 1 on first use."""

    def __init__(self, service: str = "artha-ledger", backend: Any = None, *, create: bool = True):
        self._service = service
        self._kr = backend
        self._cache: dict[int, bytes] = {}
        self._create = create

    def _keyring(self) -> Any:
        if self._kr is None:
            self._kr = system_keyring()
        return self._kr

    def _get(self, name: str) -> str | None:
        try:
            value = self._keyring().get_password(self._service, name)
        except KeyUnavailableError:
            raise
        except Exception as exc:
            raise KeyUnavailableError(f"credential store unavailable: {type(exc).__name__}") from None
        if value is not None and not isinstance(value, str):
            raise KeyUnavailableError('credential store record has an invalid type')
        return value

    def _set(self, name: str, value: str) -> None:
        try:
            self._keyring().set_password(self._service, name, value)
        except Exception as exc:
            raise KeyUnavailableError(f"credential store unavailable: {type(exc).__name__}") from None

    def current_version(self) -> int:
        raw = self._get("current_version")
        if raw is not None:
            version = self._parse_version(raw)
            if not self._create or not self._has_pending_version(version):
                return version
        elif not self._create:
            raise KeyUnavailableError("existing credential-store key required for inspection")
        with self._mutation_lock():
            return self._ensure_current_version()

    @staticmethod
    def _parse_version(raw: str) -> int:
        try:
            version = int(raw)
            if not 1 <= version <= _MAX_VERSION or str(version) != raw:
                raise ValueError
            return version
        except ValueError:
            raise KeyUnavailableError("corrupt key version record") from None

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        # The OS service is shared across ledgers and local runtime directories.
        filename = hashlib.sha256(self._service.encode()).hexdigest() + '.lock'
        path = default_installation_dir() / 'key-locks' / filename
        try:
            with installation_fence(default_installation_dir()), session_lock(path):
                yield
        except (SessionBusy, OSError):
            raise KeyUnavailableError('data-key update unavailable; retry after the active update finishes') from None

    def _ensure_current_version(self) -> int:
        raw = self._get('current_version')
        if raw is not None:
            version = self._parse_version(raw)
            self._finish_pending_version(version)
            return version
        self._publish_version(1)
        return 1

    def _has_pending_version(self, version: int) -> bool:
        from .recovery_registry import has_pending, profile_for  # noqa: PLC0415

        profile = profile_for(self._service)
        head_pending = has_pending(profile, 'key_head', self._service, 'current_version', str(version))
        key_pending = has_pending(profile, 'key', self._service, f'key.{version}', str(version))
        return head_pending or key_pending

    def _finish_pending_version(self, version: int) -> bool:
        if not self._has_pending_version(version):
            return False
        self._cache.pop(version, None)
        self.key(version)
        self._publish_version(version)
        return True

    def _publish_version(self, version: int) -> None:
        from .recovery_registry import mutation, profile_for  # noqa: PLC0415

        name = f'key.{version}'
        profile = profile_for(self._service)
        # A crash before publishing may leave a key. Reuse it; keys are immutable.
        raw = self._get(name)
        with mutation(profile, 'key', self._service, name, str(version)):
            if raw is None:
                raw = base64.b64encode(secrets.token_bytes(_KEY_BYTES)).decode('ascii')
                self._set(name, raw)
            if self._get(name) != raw:
                raise KeyUnavailableError('credential store did not persist the data key')
            self._cache.pop(version, None)
            self.key(version)
        with mutation(profile, 'key_head', self._service, 'current_version', str(version)):
            self._set('current_version', str(version))
            if self._get('current_version') != str(version):
                raise KeyUnavailableError('credential store did not persist the key version')

    def key(self, version: int) -> bytes:
        if type(version) is not int or not 1 <= version <= _MAX_VERSION:
            raise KeyUnavailableError('invalid data key version')
        if version in self._cache:
            return self._cache[version]
        raw = self._get(f"key.{version}")
        if raw is None:
            raise KeyUnavailableError(f"no data key for version {version}")
        try:
            material = base64.b64decode(raw, validate=True)
        except ValueError as exc:
            raise KeyUnavailableError("corrupt data key record") from exc
        if len(material) != _KEY_BYTES:
            raise KeyUnavailableError("data key has the wrong length")
        self._cache[version] = material
        return material

    def rotate(self) -> int:
        if not self._create:
            raise KeyUnavailableError("inspection cannot rotate keys")
        with self._mutation_lock():
            raw = self._get('current_version')
            if raw is None:
                current = self._ensure_current_version()
            else:
                current = self._parse_version(raw)
                if self._finish_pending_version(current):
                    return current
            if current == _MAX_VERSION:
                raise KeyUnavailableError('data key version limit reached')
            self._publish_version(current + 1)
            return current + 1


class EnvelopeCipher:
    def __init__(self, provider: KeyProvider):
        self._provider = provider

    def encrypt(self, plaintext: bytes, aad: bytes = b"") -> tuple[bytes, int]:
        """Return ``(envelope, key_version)``."""
        version = self._provider.current_version()
        nonce = os.urandom(_NONCE_BYTES)
        sealed = AESGCM(self._provider.key(version)).encrypt(nonce, plaintext, aad)
        return _MAGIC + version.to_bytes(4, "big") + nonce + sealed, version

    def decrypt(self, envelope: bytes, aad: bytes = b"") -> bytes:
        if len(envelope) < len(_MAGIC) + 4 + _NONCE_BYTES + 16 or not envelope.startswith(_MAGIC):
            raise DecryptionError("not an AE1 envelope")
        version = int.from_bytes(envelope[3:7], "big")
        nonce = envelope[7 : 7 + _NONCE_BYTES]
        body = envelope[7 + _NONCE_BYTES :]
        try:
            return AESGCM(self._provider.key(version)).decrypt(nonce, body, aad)
        except InvalidTag as exc:
            raise DecryptionError("authentication failed") from exc

    # JSON convenience (text columns)
    def encrypt_json(self, value: Any, aad: bytes = b"") -> tuple[str, int]:
        envelope, version = self.encrypt(json.dumps(value, sort_keys=True).encode("utf-8"), aad)
        return base64.b64encode(envelope).decode("ascii"), version

    def decrypt_json(self, text: str, aad: bytes = b"") -> Any:
        try:
            envelope = base64.b64decode(text, validate=True)
        except ValueError as exc:
            raise DecryptionError("not base64") from exc
        return json.loads(self.decrypt(envelope, aad))
