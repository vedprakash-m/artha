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
import json
import os
import secrets
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = [
    "DecryptionError", "EnvelopeCipher", "KeyProvider", "KeyUnavailableError",
    "KeyringKeyProvider", "StaticKeyProvider",
]

_MAGIC = b"AE1"
_NONCE_BYTES = 12
_KEY_BYTES = 32


class KeyUnavailableError(RuntimeError):
    """The data key cannot be obtained.  Callers block; they never write plaintext."""


class DecryptionError(RuntimeError):
    """Ciphertext failed authentication (tampered, wrong key, wrong record)."""


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

    def __init__(self, service: str = "artha-ledger", backend: Any = None):
        self._service = service
        self._kr = backend
        self._cache: dict[int, bytes] = {}

    def _keyring(self) -> Any:
        if self._kr is None:
            try:
                import keyring  # noqa: PLC0415
            except ImportError as exc:       # pragma: no cover - keyring is a hard dependency
                raise KeyUnavailableError("keyring library unavailable") from exc
            self._kr = keyring
        return self._kr

    def _get(self, name: str) -> str | None:
        try:
            value = self._keyring().get_password(self._service, name)
        except Exception as exc:
            raise KeyUnavailableError(f"credential store unavailable: {type(exc).__name__}") from exc
        return value if isinstance(value, str) else None

    def _set(self, name: str, value: str) -> None:
        try:
            self._keyring().set_password(self._service, name, value)
        except Exception as exc:
            raise KeyUnavailableError(f"credential store unavailable: {type(exc).__name__}") from exc

    def current_version(self) -> int:
        raw = self._get("current_version")
        if raw is None:
            # First use: create key version 1 and verify it can be read back.
            self._set("key.1", base64.b64encode(secrets.token_bytes(_KEY_BYTES)).decode("ascii"))
            self._set("current_version", "1")
            if self._get("current_version") != "1":
                raise KeyUnavailableError("credential store did not persist the key")
            return 1
        try:
            return int(raw)
        except ValueError as exc:
            raise KeyUnavailableError("corrupt key version record") from exc

    def key(self, version: int) -> bytes:
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
        new_version = self.current_version() + 1
        self._set(f"key.{new_version}", base64.b64encode(secrets.token_bytes(_KEY_BYTES)).decode("ascii"))
        self._set("current_version", str(new_version))
        self._cache.clear()
        return new_version


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
