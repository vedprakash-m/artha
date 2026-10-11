"""Identifiers and keyed hashes (re-artha §7.8).

* Signals: ``sig_`` + first 16 hex of an HMAC over the length-prefixed tuple
  ``(connector, account_scope, upstream_id, version_token)``.
* Content markers are keyed (HMAC), never plain hashes of low-entropy private text.
* Facts, Alerts, proposals and journals use time-ordered unique IDs (sortable by
  creation time).
* Raw evidence has its own ``evd_`` namespace and never reuses ambient-planning
  ``SIG-NNN`` identifiers.
* A collision is a hard failure.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass

__all__ = [
    "CollisionGuard",
    "HmacKey",
    "IdCollisionError",
    "content_hmac",
    "evidence_id",
    "new_id",
    "signal_id",
    "version_token",
]

_MIN_KEY_BYTES = 16


class IdCollisionError(RuntimeError):
    """Two different canonical tuples produced the same identifier."""


@dataclass(frozen=True)
class HmacKey:
    """A versioned HMAC key.  Rotating the key re-keys identifiers (a migration)."""

    version: int
    secret: bytes

    def __post_init__(self) -> None:
        if len(self.secret) < _MIN_KEY_BYTES:
            raise ValueError("HMAC key must be at least 16 bytes")
        if self.version < 1:
            raise ValueError("key version must be >= 1")


def _canonical(parts: tuple[str, ...]) -> bytes:
    """Length-prefixed encoding so no delimiter in a member can forge another tuple."""
    out = bytearray()
    for part in parts:
        raw = part.encode("utf-8")
        out += len(raw).to_bytes(4, "big") + raw
    return bytes(out)


def _mac(key: HmacKey, domain: str, parts: tuple[str, ...]) -> str:
    payload = domain.encode("ascii") + b"\x00" + _canonical(parts)
    return hmac.new(key.secret, payload, hashlib.sha256).hexdigest()


def signal_id(
    key: HmacKey, connector: str, account_scope: str, upstream_id: str, version_token_: str
) -> str:
    parts = (connector, account_scope, upstream_id, version_token_)
    if not all(parts):
        raise ValueError("signal identity members must be non-empty")
    return "sig_" + _mac(key, "signal", parts)[:16]


def evidence_id(key: HmacKey, locator: str) -> str:
    if not locator:
        raise ValueError("evidence locator must be non-empty")
    return "evd_" + _mac(key, "evidence", (locator,))[:16]


def content_hmac(key: HmacKey, text: str) -> str:
    """Keyed integrity marker for a content body."""
    return _mac(key, "content", (text,))


def version_token(
    provider_version: str | None,
    content_hmac_: str,
    known_content_hmac: str | None = None,
) -> str:
    """Version component of a Signal identity.

    The provider's version is used when it is reliable.  When the provider reports
    the same version but the keyed content marker differs from the one recorded
    for it, the content really changed: a new version is minted.  Without a
    provider version the content marker itself is the version.
    """
    if not provider_version:
        return "c:" + content_hmac_[:16]
    if known_content_hmac is not None and known_content_hmac != content_hmac_:
        return f"{provider_version}+{content_hmac_[:8]}"
    return provider_version


def new_id(prefix: str, now_ms: int | None = None, rand: bytes | None = None) -> str:
    """Time-ordered unique ID: ``<prefix>_<12 hex ms><8 hex random>``."""
    if not prefix or not prefix.isalnum():
        raise ValueError("prefix must be alphanumeric")
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    entropy = os.urandom(4) if rand is None else rand
    return f"{prefix}_{ms:012x}{entropy.hex()}"


class CollisionGuard:
    """Remembers identifier → tuple and fails hard on a different tuple."""

    def __init__(self) -> None:
        self._seen: dict[str, tuple[str, ...]] = {}

    def check(self, identifier: str, canonical_tuple: tuple[str, ...]) -> None:
        prior = self._seen.setdefault(identifier, canonical_tuple)
        if prior != canonical_tuple:
            raise IdCollisionError(f"identifier {identifier} maps to two different tuples")
