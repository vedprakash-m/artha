"""Authenticated encrypted privacy checkpoints outside the runtime's restorable set."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from catchup_core.crypto import EnvelopeCipher

ANCHOR_SCHEMA = 'artha.privacy-anchor.v2'
_HISTORY_SCHEMA = 'artha.privacy-history.v1'
_MAX_HISTORY_BYTES = 32 * 1024 * 1024
_MAX_CAPSULE_BYTES = 64 * 1024 * 1024
_DIGEST = re.compile(r'[0-9a-f]{64}')


def checkpoint_data(data: Mapping[str, Any]) -> dict[str, Any]:
    out = {name: data[name] for name in ('checkpoint_id', 'tombstone_digest', 'permission_generation')}
    for name, minimum in (('checkpoint_id', 1), ('permission_generation', 0)):
        if type(out[name]) is not int or not minimum <= out[name] <= 2**63 - 1:
            raise ValueError('invalid privacy checkpoint generation')
    if not isinstance(out['tombstone_digest'], str) or not _DIGEST.fullmatch(out['tombstone_digest']):
        raise ValueError('invalid privacy checkpoint digest')
    return out


def capsule_digest(record: Mapping[str, Any]) -> str:
    value = record.get('capsule_sha256')
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError('invalid privacy capsule reference')
    return value


def checked_history(history: Any, checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(history, dict) or set(history) - {'tombstones', 'consent'}:
        raise ValueError('invalid privacy history')
    # Detach the callback's mutable objects before validation, hashing or encryption.
    data: dict[str, Any] = json.loads(json.dumps({'tombstones': history.get('tombstones', []),
                                 'consent': history.get('consent', [])}, allow_nan=False))
    for group, identity in (('tombstones', 'tombstone_id'), ('consent', 'consent_id')):
        rows = data[group]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('invalid privacy history rows')
        keys = [row.get(identity) for row in rows]
        if (any(not isinstance(key, str) or not key or len(key) > 128 for key in keys)
                or len(set(keys)) != len(keys)):
            raise ValueError('invalid or duplicate privacy history identities')
    digest = hashlib.sha256('\n'.join(sorted(row['tombstone_id'] for row in data['tombstones'])).encode()).hexdigest()
    versions = [row.get('revocation_version') for row in data['consent']]
    if (digest != checkpoint['tombstone_digest'] or any(type(v) is not int or v < 0 for v in versions)
            or sum(versions) != checkpoint['permission_generation']):
        raise ValueError('privacy history differs from its checkpoint')
    return data


def require_continuity(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    for group, identity in (('tombstones', 'tombstone_id'), ('consent', 'consent_id')):
        incoming = {row[identity]: row for row in current[group]}
        for before in previous[group]:
            after = incoming.get(before[identity])
            if after is None:
                raise ValueError('privacy history cannot lose retained identities')
            if group == 'tombstones':
                if before != after:
                    raise ValueError('a tombstone cannot be rewritten')
            else:
                immutable = (set(before) | set(after)) - {'revoked_at', 'revocation_version'}
                if (any(before.get(key) != after.get(key) for key in immutable)
                        or after['revocation_version'] < before['revocation_version']
                        or (before.get('revoked_at') is not None and after.get('revoked_at') != before['revoked_at'])
                        or (after['revocation_version'] > before['revocation_version']
                            and not after.get('revoked_at'))):
                    raise ValueError('consent history cannot be reinterpreted or unrevoked')


class PrivacyCapsules:
    def __init__(self, directory: Path, service: str, cipher: EnvelopeCipher):
        self.directory = directory
        self._aad = ('artha.privacy-history:' + service).encode()
        self._cipher = cipher

    def load(self, record: Mapping[str, Any]) -> dict[str, Any]:
        digest = capsule_digest(record)
        with (self.directory / (digest + '.ae1')).open('rb') as handle:
            data = handle.read(_MAX_CAPSULE_BYTES + 1)
        if len(data) > _MAX_CAPSULE_BYTES or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('privacy capsule bytes differ from their trusted anchor')
        plain = self._cipher.decrypt(data, aad=self._aad)
        if len(plain) > _MAX_HISTORY_BYTES:
            raise ValueError('privacy history exceeds its supported capsule limit')
        decoded = json.loads(plain)
        expected = checkpoint_data(record)
        if (not isinstance(decoded, dict) or decoded.get('schema') != _HISTORY_SCHEMA
                or decoded.get('checkpoint') != expected):
            raise ValueError('privacy capsule does not match its trusted checkpoint')
        return checked_history(decoded.get('history'), expected)

    def save(self, checkpoint: Mapping[str, Any], history: Mapping[str, Any]) -> dict[str, Any]:
        from catchup_core.backup import atomic_bytes  # noqa: PLC0415
        plain = json.dumps({'schema': _HISTORY_SCHEMA, 'checkpoint': checkpoint, 'history': history},
                           sort_keys=True, allow_nan=False).encode()
        if len(plain) > _MAX_HISTORY_BYTES:
            raise ValueError('privacy history exceeds its supported capsule limit')
        encrypted, _ = self._cipher.encrypt(plain, aad=self._aad)
        record = {**checkpoint, 'schema': ANCHOR_SCHEMA, 'capsule_sha256': hashlib.sha256(encrypted).hexdigest()}
        atomic_bytes(self.directory / (record['capsule_sha256'] + '.ae1'), encrypted)
        if self.load(record) != history:
            raise ValueError('privacy capsule publication could not be verified')
        return record

    def prune(self, current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> None:
        from contextlib import suppress  # noqa: PLC0415
        expired = (previous or {}).get('previous_capsule_sha256')
        keep = {current['capsule_sha256'], (previous or {}).get('capsule_sha256')}
        if isinstance(expired, str) and _DIGEST.fullmatch(expired) and expired not in keep:
            # Only the OS-anchored predecessor is ours to retire. Unknown files may be recovery material.
            with suppress(OSError):
                (self.directory / (expired + '.ae1')).unlink(missing_ok=True)
