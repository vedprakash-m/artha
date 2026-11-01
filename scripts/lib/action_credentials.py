"""Provider credentials for one trusted action child, carried only through its private input pipe.

This scopes normal Python credential APIs. It is not an OS sandbox against arbitrary native code running as the same
OS user. Approval/claim authority and provider egress remain in action_process; no credential is sent to a model.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, NoReturn

from keyring.backend import KeyringBackend

from lib.credentials import SERVICE, _CHUNK, _SCHEMA, system_keyring

_SCHEMA_CAP = 'artha.action-credentials.v1'
_RECORDS = {
    'actions.email_send': 'google.gmail-oauth-token',
    'actions.email_reply': 'google.gmail-oauth-token',
    'actions.calendar_create': 'google.gcal-oauth-token',
    'actions.calendar_modify': 'google.gcal-oauth-token',
    'actions.reminder_create': 'msgraph.personal',
    'actions.todo_sync_action': 'msgraph.personal',
}
_API_KEYS = {'actions.slack_send': 'artha-slack-bot-token', 'actions.todoist_sync': 'artha-todoist-token'}
_FIELDS = {'schema', 'module', 'payload_hash', 'operation', 'since', 'nonce', 'issued_at', 'expires_at', 'records', 'api_keys'}


class CredentialCapabilityError(RuntimeError):
    pass


def _reject() -> NoReturn:
    raise CredentialCapabilityError('action credential capability is invalid or expired')


def _secret(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 32000 and bool(value.strip())


def _expiry(value: Any) -> float:
    if not isinstance(value, str):
        _reject()
    try:
        expiry = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if expiry.tzinfo is None:
            _reject()
        return expiry.timestamp()
    except (ValueError, OverflowError):
        _reject()


def _handler_matches(module: str, proposal: Any) -> bool:
    from actions import _HANDLER_MAP
    return _HANDLER_MAP.get(proposal.action_type) == module


def prepare(module: str, proposal: Any, timeout: int, *, operation: str = 'execute', since: str = '') -> dict[str, Any]:
    """Capture only after the caller verifies the exact approved queue claim; never open interactive login."""
    if (type(timeout) is not int or not 1 <= timeout <= 120 or not _handler_matches(module, proposal)
            or operation not in {'execute', 'reconcile'} or not isinstance(since, str)):
        _reject()
    records, api_keys = {}, {}
    try:
        name = _RECORDS.get(module)
        if name and name.startswith('google.'):
            from google_auth import _refresh_existing
            creds, _ = _refresh_existing(name.removeprefix('google.'), timeout + 305)
            expiry = creds.expiry
            if expiry is None:
                _reject()
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            records[name] = {'token': creds.token, 'expiry': expiry.isoformat()}
        elif name:
            from setup_msgraph_oauth import ensure_valid_token
            token = ensure_valid_token(warn_seconds=timeout + 305)
            records[name] = {'access_token': token.get('access_token'), 'expiry': token.get('expiry')}
        elif module in _API_KEYS:
            account = _API_KEYS[module]
            api_keys[account] = system_keyring().get_password('artha', account)
        elif module != 'actions.instruction_sheet':
            _reject()
        from action_queue import compute_payload_hash
        issued = time.time()
        capability = {'schema': _SCHEMA_CAP, 'module': module, 'payload_hash': compute_payload_hash(proposal),
            'operation': operation, 'since': since, 'nonce': secrets.token_hex(16), 'issued_at': issued,
            'expires_at': issued + timeout + 2, 'records': records, 'api_keys': api_keys}
        validate(capability, module, proposal, operation, since)
        return capability
    except Exception:
        raise CredentialCapabilityError('action credentials unavailable; retry or authenticate through explicit setup') from None


def validate(capability: Any, module: str, proposal: Any, operation: str, since: str) -> None:
    """Reject altered bindings, extra providers and reusable grants before importing a handler."""
    from action_queue import compute_payload_hash
    if (not isinstance(capability, dict) or set(capability) != _FIELDS or capability['schema'] != _SCHEMA_CAP
            or capability['module'] != module or not _handler_matches(module, proposal)
            or capability['payload_hash'] != compute_payload_hash(proposal)
            or capability['operation'] != operation or operation not in {'execute', 'reconcile'}
            or capability['since'] != since or not isinstance(since, str) or len(since) > 128
            or not isinstance(capability['nonce'], str) or not re.fullmatch('[a-f0-9]{32}', capability['nonce'])):
        _reject()
    issued, expires = capability['issued_at'], capability['expires_at']
    if (type(issued) not in (int, float) or type(expires) not in (int, float)
            or not math.isfinite(issued) or not math.isfinite(expires)
            or not 0 < expires - issued <= 122 or issued > time.time() + .1 or expires <= time.time()):
        _reject()
    records, api_keys = capability['records'], capability['api_keys']
    name, account = _RECORDS.get(module), _API_KEYS.get(module)
    if (not isinstance(records, dict) or not isinstance(api_keys, dict)
            or set(records) - ({name} if name else set()) or set(api_keys) - ({account} if account else set())):
        _reject()
    # Empty scope is safe for offline diagnostics: it cannot acquire any OS credential.
    for record in records.values():
        token_field = 'token' if name and name.startswith('google.') else 'access_token'
        if (not isinstance(record, dict) or set(record) != {token_field, 'expiry'}
                or not _secret(record[token_field]) or _expiry(record['expiry']) <= expires + 300):
            _reject()
    if any(not _secret(value) for value in api_keys.values()):
        _reject()


class _ScopedKeyring(KeyringBackend):
    priority = 1

    def __init__(self, capability: dict[str, Any]) -> None:
        self._expires = capability['expires_at']
        self._values = {('artha', account): value for account, value in capability['api_keys'].items()}
        for name, record in capability['records'].items():
            text = json.dumps(record, sort_keys=True, ensure_ascii=True, allow_nan=False)
            generation = secrets.token_hex(16)
            chunks = [text[i:i + _CHUNK] for i in range(0, len(text), _CHUNK)]
            self._values[SERVICE, name] = json.dumps({'schema': _SCHEMA, 'generation': generation,
                'chunks': len(chunks), 'digest': hashlib.sha256(text.encode()).hexdigest(), 'retired': []})
            for index, chunk in enumerate(chunks):
                self._values[SERVICE, f'{name}.chunk.{generation}.{index}'] = chunk

    def get_password(self, service: str, username: str) -> str | None:
        if time.time() >= self._expires:
            _reject()
        return self._values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        raise PermissionError('action credentials are read-only')

    def delete_password(self, service: str, username: str) -> None:
        raise PermissionError('action credentials are read-only')


def install(capability: Any, module: str, proposal: Any, operation: str, since: str) -> None:
    """Install once in a short-lived trusted child before any provider handler is imported."""
    validate(capability, module, proposal, operation, since)
    import keyring
    from catchup_core import crypto
    if crypto._action_keyring is not None:
        _reject()
    backend = _ScopedKeyring(capability)
    keyring.set_keyring(backend)
    crypto._action_keyring = backend


def redact(value: Any, capability: dict[str, Any]) -> Any:
    """Keep credential values out of even a provider error's returned receipt."""
    secrets_to_hide = list(capability['api_keys'].values()) + [record.get('token', record.get('access_token'))
        for record in capability['records'].values()]
    def clean(item: Any) -> Any:
        if isinstance(item, str):
            for secret in secrets_to_hide:
                item = item.replace(secret, '[credential redacted]')
            return item
        if isinstance(item, dict):
            return {clean(key): clean(content) for key, content in item.items()}
        if isinstance(item, list):
            return [clean(content) for content in item]
        return item
    return clean(value)
