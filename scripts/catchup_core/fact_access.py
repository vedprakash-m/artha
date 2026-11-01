"""Value-free Fact authorization shared by the Ledger and the scoped action worker.

These helpers grant no database authority: callers must establish their own trusted
connection, resolved household policy and current privacy checkpoint first.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AccessPolicy:
    owner: str
    consent_subjects: frozenset[str]
    known_subjects: frozenset[str] | None


@dataclass(frozen=True)
class FactMetadata:
    fact_id: str
    domain: str
    primary_subject_id: str
    status: str
    provenance_class: str
    valid_from: str | None
    valid_to: str | None
    validity_unknown: bool
    observed_at: str
    recorded_at: str


def consent_active(conn: sqlite3.Connection, policy: AccessPolicy, subject: str, operation: str,
                   audience: str, scope: str | None, now: str) -> bool:
    instant = datetime.fromisoformat(now.replace('Z', '+00:00'))
    for row in conn.execute('SELECT operations,audience,expires_at,scope,grantor_id FROM consent '
                            'WHERE subject_id=? AND revoked_at IS NULL', (subject,)):
        if subject in policy.consent_subjects and row['grantor_id'] != subject:
            continue
        if scope is not None and row['scope'] not in (scope, '*'):
            continue
        if row['expires_at'] and datetime.fromisoformat(row['expires_at'].replace('Z', '+00:00')) <= instant:
            continue
        if operation in json.loads(row['operations']) and row['audience'] in (audience, '*'):
            return True
    return False


def subjects_for(conn: sqlite3.Connection, row: sqlite3.Row) -> set[str]:
    return {str(row['primary_subject_id']), *(str(r[0]) for r in conn.execute(
        'SELECT subject_id FROM fact_subjects WHERE fact_id=?', (row['fact_id'],)))}


def source_scopes(conn: sqlite3.Connection, fact_id: str) -> list[tuple[str, str]]:
    return [(str(row[0]), str(row[1])) for row in conn.execute(
        'SELECT DISTINCT s.connector,s.account_scope FROM signals s JOIN '
        '(SELECT signal_id FROM fact_evidence WHERE fact_id=? UNION '
        'SELECT signal_id FROM fact_version_evidence WHERE fact_id=?) e USING(signal_id)', (fact_id, fact_id))]


def sources_allowed(conn: sqlite3.Connection, policy: AccessPolicy, fact_id: str, subjects: set[str],
                    viewer: str, operation: str, now: str) -> bool:
    return all(consent_active(conn, policy, subject, operation, viewer, f'{connector}:{scope}', now)
               for connector, scope in source_scopes(conn, fact_id)
               for subject in (subjects & policy.consent_subjects) - {viewer})


def may_view(conn: sqlite3.Connection, policy: AccessPolicy, row: sqlite3.Row, viewer: str,
             now: str) -> bool:
    fact_id = row['fact_id']
    subjects = subjects_for(conn, row)
    if policy.known_subjects is not None and not subjects <= policy.known_subjects:
        return False
    if conn.execute('SELECT 1 FROM signals s JOIN tombstones t ON '
            '(s.connector=t.connector AND s.account_scope=t.account_scope AND s.upstream_id=t.upstream_id) '
            'OR s.content_hmac=t.content_hmac WHERE s.signal_id IN '
            '(SELECT signal_id FROM fact_evidence WHERE fact_id=? UNION '
            'SELECT signal_id FROM fact_version_evidence WHERE fact_id=?) LIMIT 1', (fact_id, fact_id)).fetchone():
        return False
    if conn.execute('SELECT 1 FROM fact_evidence e JOIN signals s ON e.signal_id=s.signal_id '
            'JOIN calendar_cancellations c ON s.connector=c.connector AND s.account_scope=c.account_scope '
            'AND s.upstream_id=c.upstream_id WHERE e.fact_id=? AND c.pending=1', (fact_id,)).fetchone():
        return False
    if not (viewer in (policy.owner, row['primary_subject_id']) or conn.execute(
            'SELECT 1 FROM fact_viewers WHERE fact_id=? AND viewer_id=?', (fact_id, viewer)).fetchone()):
        return False
    if any(not consent_active(conn, policy, subject, 'view', viewer, str(row['domain']), now)
           for subject in (subjects & policy.consent_subjects) - {viewer}):
        return False
    return sources_allowed(conn, policy, fact_id, subjects, viewer, 'view', now)


def require_metadata(conn: sqlite3.Connection, policy: AccessPolicy, fact_id: str, viewer: str,
                     now: str, *, operation: str = 'view', source_operation: bool = False) -> FactMetadata:
    """Authorize metadata without selecting or decrypting any source value."""
    from catchup_core.ledger import LedgerError, PermissionDenied  # noqa: PLC0415
    row = conn.execute('SELECT fact_id,domain,primary_subject_id,status,provenance_class,'
                       'valid_from,valid_to,validity_unknown,'
                       'observed_at,recorded_at FROM facts WHERE fact_id=?', (fact_id,)).fetchone()
    if row is None:
        raise LedgerError(f'unknown fact {fact_id}')
    if not may_view(conn, policy, row, viewer, now):
        raise PermissionDenied(f'{viewer} may not view {fact_id}')
    subjects = subjects_for(conn, row)
    if any(not consent_active(conn, policy, subject, operation, viewer, str(row['domain']), now)
           for subject in (subjects & policy.consent_subjects) - {viewer}):
        raise PermissionDenied(f'{operation} requires each adult subject\'s scoped consent')
    if source_operation and not sources_allowed(conn, policy, fact_id, subjects, viewer, operation, now):
        raise PermissionDenied(f'{operation} requires source-scoped consent')
    values: dict[str, Any] = dict(row)
    values['status'], values['provenance_class'] = document_fact_state(conn, policy, row, now)
    values['validity_unknown'] = bool(values['validity_unknown'])
    return FactMetadata(**values)


def document_link_proof(conn: sqlite3.Connection, link: dict[str, Any]) -> str:
    material: list[Any] = [{name: value for name, value in link.items() if name != 'proof'}]
    queries = (
        ('SELECT * FROM facts WHERE fact_id=?', link['fact_id']),
        ('SELECT * FROM signals WHERE signal_id=?', link['signal_id']),
        ('SELECT * FROM document_field_requirements WHERE binding_id=?', link['binding_id']),
        ('SELECT * FROM document_field_confirmations WHERE binding_id=?', link['binding_id']),
    )
    for query, identity in queries:
        row = conn.execute(query, (identity,)).fetchone()
        if row is None:
            return ''
        columns = dict(row)
        if 'signal_id' in columns and 'processing_state' in columns:
            material.append({name: row[name] for name in ('signal_id', 'connector', 'account_scope',
                'upstream_id', 'upstream_version', 'content_hmac', 'evidence_blob_ref', 'span_unit')})
        else:
            material.append(dict(row))
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def document_fact_state(conn: sqlite3.Connection, policy: AccessPolicy, row: sqlite3.Row,  # noqa: PLR0912
                        now: str) -> tuple[str, str]:
    status, provenance = str(row['status']), str(row['provenance_class'])
    if provenance == 'user_confirmed' or status not in ('current', 'unknown'):
        return status, provenance
    fact_id = row['fact_id']
    documents = conn.execute('SELECT s.signal_id FROM fact_evidence e JOIN signals s USING(signal_id) '
                             "WHERE e.fact_id=? AND s.connector='documents'", (fact_id,)).fetchall()
    available = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_field_links'")
    if not available.fetchone():
        return ('unknown', 'model_hypothesis') if documents else (status, provenance)
    marked = conn.execute('SELECT 1 FROM document_field_facts WHERE fact_id=?', (fact_id,)).fetchone()
    uncertain = any(not conn.execute("SELECT 1 FROM document_sources WHERE signal_id=? AND method='text'",
                                    (source[0],)).fetchone() for source in documents)
    if not marked and not uncertain:
        return status, provenance
    for found in conn.execute('SELECT * FROM document_field_links WHERE fact_id=?', (fact_id,)):
        link = dict(found)
        source = conn.execute('SELECT * FROM signals WHERE signal_id=?', (link['signal_id'],)).fetchone()
        receipt = conn.execute('SELECT state,signal_id FROM document_field_confirmations WHERE binding_id=?',
                               (link['binding_id'],)).fetchone()
        if (source is None or receipt is None or receipt['state'] != 'confirmed'
                or receipt['signal_id'] != link['signal_id'] or source['connector'] != 'documents'
                or source['processing_state'] == 'excluded' or source['span_unit'] != 'utf8-byte'
                or not source['evidence_blob_ref'] or link['reviewer'] != policy.owner
                or link['fact_version'] != row['recorded_at'] or link['domain'] != row['domain']
                or link['subject_id'] != row['primary_subject_id']
                or source['upstream_version'] != link['signal_version']
                or source['content_hmac'] != link['provenance_hmac']):
            continue
        if not conn.execute('SELECT 1 FROM source_heads WHERE signal_id=? AND complete=1',
                            (link['signal_id'],)).fetchone():
            continue
        if not conn.execute('SELECT 1 FROM fact_evidence WHERE fact_id=? AND signal_id=? '
                            'AND span_start=? AND span_end=?', (fact_id, link['signal_id'],
                             link['span_start'], link['span_end'])).fetchone():
            continue
        attribution = conn.execute('SELECT subjects FROM signal_access WHERE signal_id=?',
                                   (link['signal_id'],)).fetchone()
        subjects = subjects_for(conn, row)
        try:
            source_subjects = json.loads(attribution[0]) if attribution else []
        except (TypeError, ValueError):
            continue
        if (not isinstance(source_subjects, list) or not source_subjects
                or any(not isinstance(subject, str) or not subject for subject in source_subjects)
                or link['subject_id'] not in source_subjects):
            continue
        subjects.update(source_subjects)
        if json.dumps(sorted(subjects), separators=(',', ':')) != link['scope']:
            continue
        if policy.known_subjects is not None and not subjects <= policy.known_subjects:
            continue
        if any(not consent_active(conn, policy, subject, operation, policy.owner, scope, now)
               for subject in (subjects & policy.consent_subjects) - {policy.owner}
               for operation in ('view', 'correct')
               for scope in (link['domain'], f"documents:{source['account_scope']}")):
            continue
        if hmac.compare_digest(link['proof'], document_link_proof(conn, link)):
            return 'current', 'verified_source'
    return 'unknown', 'model_hypothesis'
