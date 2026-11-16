"""Opt-in renewal reader. Never opens a Ledger, source payload or provider connection."""
from __future__ import annotations

import os
import socket
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from catchup_core.ledger import PermissionDenied
from catchup_core.renewal_authority import Authority
from catchup_core.renewal_metadata import metadata_for
from catchup_core.renewal_store import RenewalError, digest, is_digest
from catchup_core.renewal_workspace import Workspace, context
from lib.credentials import CredentialError, CredentialStore


def authority_for(viewer: str) -> Authority:
    local = Path(os.environ.get('ARTHA_LOCAL_DIR') or str(Path.home() / '.artha-local')).expanduser() / 'catchup_core'
    credentials = CredentialStore(local=local)
    state, _ = credentials.read_versioned('release.renewal')
    if not isinstance(state, dict) or not isinstance(state.get('binding'), dict):
        raise RenewalError('native release enrollment required')
    binding = state['binding']
    if binding.get('owner') != viewer or binding.get('host') != socket.gethostname().split('.')[0]:
        raise RenewalError('release owner or designated host differs')
    root = Path(__file__).resolve().parents[2]
    authority = Authority(local, binding, credentials=credentials, checkout=root)
    Workspace(root).remote(binding['repository'])
    return authority


def snapshot(authority: Authority, cohort: Mapping[str, str], tree: str) -> dict[str, Any]:
    policy = authority.policy()
    extra = tuple(entry['path'] for entry in policy['base_source']['files'])
    return {'source': Workspace(Path(authority.checkout)).source(extra), 'cohort': dict(cohort),
            'context': context(Path(authority.checkout)), 'tree': tree, 'authority': metadata_for(authority).snapshot()}


def resolve(config: Mapping[str, Any], cohort: Mapping[str, str], *, viewer: str) -> Mapping[str, str]:
    lease = prepare(config, cohort, viewer=viewer)
    return lease.validate() if lease is not None else cohort


def prepare(config: Mapping[str, Any], cohort: Mapping[str, str], *, viewer: str) -> RenewalLease | None:
    release = config.get('release', {})
    if not isinstance(release, dict) or 'renewal' not in release:
        return None
    try:
        pointer = release['renewal']
        if (not isinstance(pointer, dict) or set(pointer) != {'schema', 'delegation'}
                or pointer['schema'] != 'artha.release.renewal.v1' or not is_digest(pointer['delegation'])):
            raise RenewalError('release renewal pointer is invalid')
        authority = authority_for(viewer)
        policy = authority.policy()
        if (digest(policy) != pointer['delegation']
                or {key: value for key, value in release.items() if key != 'renewal'} != policy['base_release']):
            raise RenewalError('release approval differs from enrolled baseline')
        active = authority.active()
        if active is None:
            raise RenewalError('release renewal is not yet activated')
        target = snapshot(authority, cohort, active['target']['tree'])
        # The decision records publication-time authority; current consent remains
        # independently enforced by the current metadata reader and existing data broker.
        target['authority'] = active['target']['authority']
        authority.validate(target, validate_base=lambda _: None)
        return RenewalLease(authority, target, policy['base_cohort'])
    except (RenewalError, CredentialError, OSError, ValueError, KeyError, TypeError):
        raise PermissionDenied('renewal authority, source or evidence is held; use --shadow') from None


class RenewalLease:
    """Frozen source checked at construction; runtime separately rechecks its loaded input closure.

    Each use still verifies native revocation, generation and immutable decision. This
    avoids hashing the full delivery/test tree for each runtime authorization check.
    """
    def __init__(self, authority: Authority, target: dict[str, Any], base: Mapping[str, str]):
        self.authority = authority
        self.target = target
        self.base = dict(base)
        self.connection: sqlite3.Connection | None = None

    def validate(self) -> Mapping[str, str]:
        try:
            if context(Path(self.authority.checkout)) != self.target['context']:
                raise RenewalError('renewal environment changed')
            metadata_for(self.authority).snapshot(connection=self.connection)
            self.authority.validate(self.target, validate_base=lambda _: None)
            return self.base
        except (RenewalError, CredentialError, OSError, ValueError):
            raise PermissionDenied('renewal authority or environment changed; use --shadow') from None
