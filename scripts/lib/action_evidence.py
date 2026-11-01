"""Short-lived value-free evidence authority for the trusted action worker.

The parent resolves the canonical ledger and real household authority. The worker
receives metadata policy, never ledger keys or Fact/source values. A durable queue
binding prevents the child pipe from choosing a different evidence database.
"""
from __future__ import annotations

import hashlib
import json
import os
import math
import re
import sqlite3
import time
from contextlib import contextmanager, closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from catchup_core.actions import ClaimRefused, _instant, _verify_fact, normalize_preconditions
from catchup_core.fact_access import AccessPolicy, consent_active, require_metadata

_SCHEMA = 'artha.action-evidence.v1'
_FIELDS = {'schema', 'root', 'ledger_path', 'namespace', 'payload_hash', 'expires_at', 'files',
           'owner', 'known_subjects', 'consent_subjects', 'sources', 'domains', 'head', 'household_day', 'household_timezone'}


def _files(root: Path, domains: list[str]) -> dict[str, str | None]:
    paths = ['config/catchup_core.yaml', 'config/user_profile.yaml']
    paths += [f'scripts/catchup_core/packs/{domain}.yaml' for domain in domains]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() if (root / name).is_file()
            else None for name in paths}


@contextmanager
def authorized_ledger(root: Path) -> Iterator[tuple[Any, Any, Any]]:
    """Inspection-only real Ledger context, with no key enrollment or migration."""
    from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider, system_keyring
    from catchup_core.ledger import Ledger, KeyringSealedStore
    from catchup_core.runtime import _bind_household, _id_key, load_runtime_config
    from catchup_core.session_lock import require_active_installation
    config_path = root / 'config/catchup_core.yaml'
    initial_config = hashlib.sha256(config_path.read_bytes()).hexdigest()
    config = load_runtime_config(config_path)
    files = _files(root, list(config.domains))
    if files['config/catchup_core.yaml'] != initial_config:
        raise ClaimRefused('evidence policy changed while resolving authority')
    base = Path(os.environ.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser().resolve()
    require_active_installation(root, base)
    path = base / 'catchup_core/ledger.db'
    if base.is_relative_to(root.resolve()) or not path.is_file():
        raise ClaimRefused('canonical authoritative evidence ledger is unavailable')
    backend = system_keyring()
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=backend, create=False))
    key = _id_key(KeyringKeyProvider('artha-ledger-ids', backend=backend, create=False))
    if config.policy.get('household_from_profile') is True:
        from catchup_core.runtime import _load_packs
        pack = _load_packs(config.domains)
        _bind_household(config, root, key, datetime.now(UTC).astimezone(pack.timezone).date(), enroll=False)
    with Ledger(path, id_key=key, cipher=cipher, owner_id=config.viewer,
                consent_subjects=config.consent_required,
                known_subjects=frozenset((*config.subjects, config.viewer)),
                sealed=KeyringSealedStore(backend, 'authoritative', cipher=cipher)).open(read_only=True) as ledger:
        if (ledger.schema_version() != max(n for n, _ in Ledger.available_migrations())
                or ledger.conn.execute("SELECT 1 FROM deletion_journal WHERE state<>'receipted'").fetchone()
                or not ledger.check_restore()):
            raise ClaimRefused('evidence privacy history or schema requires maintenance')
        yield ledger, config, backend
        if _files(root, list(config.domains)) != files:
            raise ClaimRefused('evidence policy changed while verifying authority')


def _head(backend: Any) -> dict[str, Any]:
    from catchup_core.ledger import KeyringSealedStore
    from dataclasses import asdict
    checkpoint = KeyringSealedStore(backend, 'authoritative').read()
    if checkpoint is None:
        raise ClaimRefused('authoritative privacy checkpoint is missing')
    return asdict(checkpoint)


def _metadata_context(root: Path, ledger: Any, config: Any, backend: Any, payload_hash: str) -> dict[str, Any]:
    from catchup_core.runtime import _load_packs
    household = getattr(config, 'policy', {}).get('household_from_profile') is True
    timezone = _load_packs(tuple(config.domains)).timezone
    return {'schema': _SCHEMA, 'root': str(root.resolve()), 'ledger_path': str(ledger._path.resolve()),
        'namespace': 'authoritative', 'payload_hash': payload_hash, 'expires_at': time.time() + 122,
        'files': _files(root, list(config.domains)), 'owner': config.viewer,
        'known_subjects': sorted(set((*config.subjects, config.viewer))),
        'consent_subjects': sorted(config.consent_required),
        'sources': {name: {'account_scope': spec['account_scope']} for name, spec in config.sources.items()},
        'domains': list(config.domains), 'head': _head(backend),
        'household_day': datetime.now(UTC).astimezone(timezone).date().isoformat() if household else None,
        'household_timezone': str(timezone) if household else None}


def require_freshness(conn: sqlite3.Connection, root: Path, config: Any, normalized: dict[str, Any],
                      now: datetime) -> None:
    """Bound declared freshness by every current source's Domain policy and current coverage."""
    from catchup_core.domain_pack import load_pack_file
    domains = config['domains'] if isinstance(config, dict) else config.domains
    sources = config['sources'] if isinstance(config, dict) else config.sources
    for fid in normalized['fact_versions']:
        row = conn.execute('SELECT domain,observed_at FROM facts WHERE fact_id=?', (fid,)).fetchone()
        if row is None or row['domain'] not in domains:
            raise ClaimRefused('action evidence Domain is not configured')
        pack = load_pack_file(root / f'scripts/catchup_core/packs/{row["domain"]}.yaml')
        bounds = {source.connector: source.freshness_hours for source in pack.sources}
        evidence = conn.execute('SELECT s.signal_id,s.connector,s.account_scope,s.upstream_id,s.observed_at '
            'FROM signals s JOIN fact_evidence e USING(signal_id) WHERE e.fact_id=?', (fid,)).fetchall()
        if not evidence:
            raise ClaimRefused('action evidence needs a declared, fresh source')
        for signal in evidence:
            name, scope = signal['connector'], signal['account_scope']
            spec = sources.get(name)
            if (name not in bounds or not isinstance(spec, dict) or spec.get('account_scope') != scope):
                raise ClaimRefused('action evidence source is outside declared Domain policy')
            coverage = conn.execute('SELECT status,completed_at FROM connector_coverage '
                'WHERE connector=? AND account_scope=?', (name, scope)).fetchone()
            if not coverage or coverage['status'] != 'complete' or not coverage['completed_at']:
                raise ClaimRefused('action evidence source coverage is incomplete')
            limit = timedelta(hours=bounds[name])
            stamps = [_instant(row['observed_at']), _instant(signal['observed_at']), _instant(coverage['completed_at'])]
            deadline = min(stamp + limit for stamp in stamps)
            if any(stamp > now for stamp in stamps) or deadline <= now or _instant(normalized['fresh_until']) > deadline:
                raise ClaimRefused('action freshness exceeds the declared Domain/source limit')
            # A newly fetched upstream version invalidates a still-current old Fact immediately,
            # including the interval before extraction/correction has finished.
            newer = conn.execute('SELECT signal_id,observed_at FROM signals WHERE connector=? '
                'AND account_scope=? AND upstream_id=? AND signal_id<>?',
                (name, scope, signal['upstream_id'], signal['signal_id']))
            if any(_instant(candidate['observed_at']) >= _instant(signal['observed_at']) for candidate in newer):
                raise ClaimRefused('action source version changed; refresh the evidence')


# Action types whose handler sends the reviewed provider version as a write condition.
# Every other registered type creates or sends something new, has no existing resource
# version to condition on, and therefore refuses a declared provider version.
_CONDITIONAL_WRITERS = {'calendar_modify': 'actions.calendar_modify'}


def has_conditional_writer(action_type: object) -> bool:
    return action_type in _CONDITIONAL_WRITERS


def require_provider_type(action_type: str, module: str | None = None) -> str:
    """Type-level check usable without readable parameters (queue approval and claim)."""
    from actions import _HANDLER_MAP
    registered = _HANDLER_MAP.get(action_type)
    if registered is None or (module is not None and module != registered):
        raise ClaimRefused('fresh provider version verification needs a registered handler')
    if _CONDITIONAL_WRITERS.get(action_type) != registered:
        raise ClaimRefused('fresh provider version verification is unsupported for this action')
    return registered


def require_provider_scope(proposal: Any, module: str | None = None) -> dict[str, Any]:
    """Bind flat resource versions to the registered action's exact reviewed target."""
    from actions import _HANDLER_MAP
    registered = _HANDLER_MAP.get(proposal.action_type)
    if registered is None or (module is not None and module != registered):
        raise ClaimRefused('fresh provider version verification needs a registered handler')
    normalized = normalize_preconditions(proposal.parameters.get('preconditions'))
    versions = normalized.get('provider_versions', {})
    if not versions:
        if normalized.get('core_derived') and has_conditional_writer(proposal.action_type):
            raise ClaimRefused('a core-derived change must bind the exact reviewed provider version')
        return normalized
    require_provider_type(proposal.action_type, module)
    params = proposal.parameters
    event, calendar, version = params.get('event_id'), params.get('calendar_id', 'primary'), params.get('expected_etag')
    if (any(not isinstance(value, str) or not value.strip() or len(value) > 1024
            or any(ord(char) < 33 or char in '/\\?#%' for char in value) for value in (event, calendar))
            or not isinstance(version, str) or not version or len(version) > 1024
            or any(ord(char) < 32 or ord(char) == 127 for char in version)
            or versions != {event: version}):
        raise ClaimRefused('fresh provider version must bind the exact reviewed calendar event and ETag')
    return normalized


def verify_provider(proposal: Any, module: str) -> None:
    """Read only a fixed registered resource through the handler's ordinary provider client."""
    normalized = require_provider_scope(proposal, module)
    if not normalized.get('provider_versions'):
        return
    from actions.calendar_modify import verify_provider_version
    try:
        verify_provider_version(proposal)
    except Exception:
        raise ClaimRefused('fresh provider version or current calendar authorization could not be verified') from None


def verify_parent(proposal: Any, root: Path, preconditions: dict[str, Any]) -> None:
    from catchup_core.actions import verify_evidence_preconditions
    require_provider_scope(proposal)
    with authorized_ledger(root) as (ledger, config, _):
        revision = (ledger.conn.total_changes, ledger.conn.execute('PRAGMA data_version').fetchone()[0])
        verify_evidence_preconditions({**preconditions, 'provider_versions': {}}, ledger=ledger, viewer=config.viewer)
        require_freshness(ledger.conn, root, config, preconditions, datetime.now(UTC))
        if (ledger.conn.total_changes, ledger.conn.execute('PRAGMA data_version').fetchone()[0]) != revision:
            raise ClaimRefused('action evidence changed during parent verification')


def prepare(proposal: Any, root: Path, preconditions: dict[str, Any]) -> dict[str, Any]:
    from action_queue import compute_payload_hash
    from catchup_core.actions import verify_evidence_preconditions
    require_provider_scope(proposal)
    with authorized_ledger(root) as (ledger, config, backend):
        revision = (ledger.conn.total_changes, ledger.conn.execute('PRAGMA data_version').fetchone()[0])
        verify_evidence_preconditions({**preconditions, 'provider_versions': {}}, ledger=ledger, viewer=config.viewer)
        require_freshness(ledger.conn, root, config, preconditions, datetime.now(UTC))
        context = _metadata_context(root, ledger, config, backend, compute_payload_hash(proposal))
        if (ledger.conn.total_changes, ledger.conn.execute('PRAGMA data_version').fetchone()[0]) != revision:
            raise ClaimRefused('action evidence changed during capability preparation')
        return context


def bind(queue_path: Path, proposal: Any, context: dict[str, Any]) -> None:
    from action_queue import compute_payload_hash
    with closing(sqlite3.connect(queue_path.resolve().as_uri() + '?mode=rw', uri=True)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS action_evidence_contexts('
                   'action_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,context_json TEXT NOT NULL)')
        db.execute('INSERT OR REPLACE INTO action_evidence_contexts VALUES(?,?,?)',
            (proposal.id, compute_payload_hash(proposal), json.dumps(context, sort_keys=True, allow_nan=False)))


def native_head_reader() -> Any:
    """Capture a single fixed metadata operation before provider-only keyring installation."""
    from catchup_core.crypto import system_keyring
    backend = system_keyring()
    return lambda: _head(backend)


class _MetadataLedger:
    def __init__(self, conn: sqlite3.Connection, context: dict[str, Any], now: str):
        self.conn = conn
        self.policy = AccessPolicy(context['owner'], frozenset(context['consent_subjects']),
                                   frozenset(context['known_subjects']))
        self._consent_subjects = self.policy.consent_subjects
        self.now = now

    def get_fact_metadata(self, fid: str, viewer: str, *, operation: str = 'view') -> Any:
        return require_metadata(self.conn, self.policy, fid, viewer, self.now,
                                operation=operation, source_operation=(operation == 'export'))

    def consent_active(self, subject: str, operation: str, viewer: str, scope: str | None = None) -> bool:
        return consent_active(self.conn, self.policy, subject, operation, viewer, scope, self.now)


def verify_worker(proposal: Any, root: Path, preconditions: dict[str, Any], context: Any,
                  queue: sqlite3.Connection, read_head: Any) -> None:
    from action_queue import compute_payload_hash
    from catchup_core.ledger import Ledger
    from catchup_core.schema_history import sources, verify
    from zoneinfo import ZoneInfo
    if (not isinstance(context, dict) or set(context) != _FIELDS or context['schema'] != _SCHEMA
            or context['namespace'] != 'authoritative' or context['root'] != str(root.resolve())
            or context['payload_hash'] != compute_payload_hash(proposal)
            or type(context['expires_at']) not in (int, float)
            or not math.isfinite(context['expires_at'])
            or not time.time() < context['expires_at'] <= time.time() + 123
            or not isinstance(context['domains'], list) or not 1 <= len(context['domains']) <= 64
            or any(not isinstance(domain, str) or not re.fullmatch('[a-z][a-z0-9_]*', domain)
                   for domain in context['domains'])
            or context['files'] != _files(root, context['domains'])):
        raise ClaimRefused('action evidence capability is invalid, changed or expired')
    bound = queue.execute('SELECT payload_hash,context_json FROM action_evidence_contexts WHERE action_id=?',
                          (proposal.id,)).fetchone()
    if (not bound or bound[0] != context['payload_hash'] or json.loads(bound[1]) != context):
        raise ClaimRefused('action evidence capability is not bound to this queue claim')
    if (context['household_day'] is not None and (not isinstance(context['household_timezone'], str)
            or datetime.now(UTC).astimezone(ZoneInfo(context['household_timezone'])).date().isoformat()
            != context['household_day'])):
        raise ClaimRefused('household policy day changed; resolve current adult consent again')
    path = Path(context['ledger_path'])
    if (not path.is_absolute() or path.name != 'ledger.db' or path.parent.name != 'catchup_core'
            or path.resolve() != path or path.is_relative_to(root.resolve())
            or preconditions.get('ledger_path', str(path)) != str(path)):
        raise ClaimRefused('action evidence namespace changed')
    if read_head is None or read_head() != context['head']:
        raise ClaimRefused('current privacy history changed or is unavailable')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        generation = conn.execute('PRAGMA data_version').fetchone()[0]
        verify(conn, sources(Ledger.available_migrations()), max(n for n, _ in Ledger.available_migrations()))
        normalized = normalize_preconditions(preconditions)
        current = datetime.now(UTC)
        if normalized.get('provider_versions'):
            require_provider_scope(proposal)
        if _instant(normalized['fresh_until']) <= current:
            raise ClaimRefused('action evidence freshness limit expired')
        permission = int(conn.execute('SELECT COALESCE(SUM(revocation_version),0) FROM consent').fetchone()[0])
        checkpoint = conn.execute('SELECT checkpoint_id,tombstone_digest,permission_generation FROM '
                                  'deletion_checkpoint ORDER BY checkpoint_id DESC LIMIT 1').fetchone()
        digest = hashlib.sha256('\n'.join(str(r[0]) for r in conn.execute(
            'SELECT tombstone_id FROM tombstones ORDER BY tombstone_id')).encode()).hexdigest()
        head = context['head']
        if (not checkpoint or dict(checkpoint) != head or head['permission_generation'] != permission
                or head['tombstone_digest'] != digest or normalized['permission_generation'] != permission
                or conn.execute("SELECT 1 FROM deletion_journal WHERE state<>'receipted'").fetchone()):
            raise ClaimRefused('current evidence privacy generation is unverified')
        metadata = _MetadataLedger(conn, context, current.isoformat())
        subjects: set[str] = set()
        for fid, stamp in normalized['fact_versions'].items():
            subjects.update(_verify_fact(metadata, fid, stamp, context['owner'], current))
        if subjects != set(normalized['subjects']):
            raise ClaimRefused('action evidence subject scope changed')
        require_freshness(conn, root, context, normalized, current)
        if conn.execute('PRAGMA data_version').fetchone()[0] != generation:
            raise ClaimRefused('action evidence changed during dispatch verification')
    if read_head() != context['head']:
        raise ClaimRefused('privacy history changed during evidence verification')
