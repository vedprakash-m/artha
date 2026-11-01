"""Namespace-local request reservations, usage receipts and bounds; not cash enforcement.

Each request is reserved (committed) before any disclosure. A reservation carries a size-based token
*estimate*. Actual provider usage arrives later as a separate immutable receipt; until it does, the
request counts at its estimate and is reported as unknown, never as zero. Spend is never refunded:
cancellation keeps reservations, and a restored older ledger is detected through a spend seal kept
outside the restorable set, so lost admissions count as uncertain spend and cannot be disclosed again.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from catchup_core.ledger import Ledger
from catchup_core.session_lock import SessionBusy, session_lock


class ResourceLimit(RuntimeError):
    """The namespace allocation or cancellation policy denies this operation."""


class PolicyRequired(ValueError):
    """Resource limits must be explicitly configured before model admission."""


def _bounded_integer(value: object, name: str, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= 2**31 - 1:
        raise ValueError(f'{name} must be an integer between {minimum} and 2147483647')
    return value


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f'{name} must be nonempty text of at most 512 characters')
    return value


def _stamp(now: datetime) -> str:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now requires an explicit timezone')
    return now.astimezone(UTC).isoformat(timespec='microseconds')


ADOPTED_LIMITS: Mapping[str, int] = {
    'max_requests_per_bundle': 16,
    'max_prompt_bytes_per_bundle': 512 * 1024,
    'max_requests_per_catchup': 64,
    'max_attempts_per_signal': 3,
    'max_priority_streak': 3,
}
_REQUIRED = ('max_requests_per_month', 'max_pending_signals')
INT_MAX = 2**31 - 1


@dataclass(frozen=True)
class ResourcePolicy:
    """Adopted defaults fill the five per-bundle/Catch-up/attempt limits; month and backlog stay explicit."""

    max_requests_per_bundle: int
    max_prompt_bytes_per_bundle: int
    max_requests_per_catchup: int
    max_requests_per_month: int
    max_attempts_per_signal: int
    max_pending_signals: int
    max_priority_streak: int
    max_tokens_per_month: int | None = None
    bytes_per_token: int = 4
    output_tokens_reserve: int = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == 'max_tokens_per_month' and value is None:
                continue
            _bounded_integer(value, field.name, 0 if field.name == 'output_tokens_reserve' else 1)

    @classmethod
    def from_mapping(cls, raw: object) -> ResourcePolicy:
        if raw is None:
            raise PolicyRequired('explicit namespace resource policy required')
        names = {field.name for field in fields(cls)}
        if not isinstance(raw, Mapping) or not set(raw) <= names or any(name not in raw for name in _REQUIRED):
            raise ValueError('resource policy requires explicit max_requests_per_month and max_pending_signals '
                             'and accepts only known limit fields')
        merged = {**ADOPTED_LIMITS, **raw}
        return cls(**{name: merged[name] for name in names if name in merged})

    def estimate_tokens(self, prompt_bytes: int) -> int:
        """Size-based estimate of one request; it is never provider usage."""
        return min(INT_MAX, -(-prompt_bytes // self.bytes_per_token) + self.output_tokens_reserve)


@dataclass(frozen=True)
class MonthUsage:
    """Counts for one UTC month. ``unknown_requests`` have no reported usage; they count at their estimate."""

    month: str
    requests: int
    reported_requests: int
    unknown_requests: int
    actual_tokens: int
    estimated_tokens: int
    uncertain_requests: int
    uncertain_tokens: int

    @property
    def counted_tokens(self) -> int:
        return self.actual_tokens + self.estimated_tokens + self.uncertain_tokens


# ── spend seal (outside the restorable set) ───────────────────────────────────

class SpendSealError(RuntimeError):
    """The sealed spend record is unreadable or cannot be updated."""


@dataclass(frozen=True)
class SpendRecord:
    serial: int
    month: str
    requests: int
    tokens: int
    keys: frozenset[str]


def _merge(existing: SpendRecord | None, record: SpendRecord) -> SpendRecord | None:
    """The record to store, or None when the stored one already covers it. Spend only grows."""
    if existing is None:
        return record
    if existing.month > record.month:
        requests, tokens = existing.requests, existing.tokens
    elif existing.month < record.month:
        requests, tokens = record.requests, record.tokens
    else:
        requests, tokens = max(existing.requests, record.requests), max(existing.tokens, record.tokens)
    merged = SpendRecord(max(existing.serial, record.serial), max(existing.month, record.month), requests,
                         tokens, existing.keys | record.keys)
    return None if merged == existing else merged


class SpendSeal(Protocol):
    def read(self) -> SpendRecord | None: ...

    def advance(self, record: SpendRecord) -> None: ...


class MemorySpendSeal:
    def __init__(self) -> None:
        self._value: SpendRecord | None = None

    def read(self) -> SpendRecord | None:
        return self._value

    def advance(self, record: SpendRecord) -> None:
        merged = _merge(self._value, record)
        if merged is not None:
            self._value = merged


class FileSpendSeal:
    """A sealed record in a private directory that no snapshot or sync root includes."""

    def __init__(self, path: Path, enroll: Callable[[], AbstractContextManager[Any]] | None = None,
                 require_existing: Callable[[], bool] | None = None):
        self._path = Path(path)
        self._enroll = enroll       # wraps first creation so the recovery registry names the file
        self._require_existing = require_existing

    def read(self) -> SpendRecord | None:
        try:
            raw = self._path.read_text(encoding='utf-8')
        except FileNotFoundError:
            try:
                required = self._require_existing is not None and self._require_existing()
            except RuntimeError as exc:
                raise SpendSealError('spend seal continuity cannot be verified') from exc
            if required:
                raise SpendSealError('previously registered spend seal is missing') from None
            return None
        except OSError as exc:
            raise SpendSealError('spend seal is unreadable') from exc
        try:
            data = json.loads(raw)
            keys = data['keys']
            if (data['version'] != 1 or not isinstance(keys, list) or not all(isinstance(k, str) for k in keys)):
                raise ValueError
            record = SpendRecord(_bounded_integer(data['serial'], 'serial', 0), str(data['month']),
                                 _bounded_integer(data['requests'], 'requests', 0),
                                 _bounded_integer(data['tokens'], 'tokens', 0), frozenset(keys))
            if len(record.month) != 7:
                raise ValueError
            return record
        except (ValueError, KeyError, TypeError) as exc:
            raise SpendSealError('spend seal is malformed') from exc

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        lock = self._path.with_name(self._path.name + '.lock')
        deadline = time.monotonic() + 30
        while True:
            try:
                with session_lock(lock):
                    yield
                return
            except SessionBusy:
                if time.monotonic() > deadline:
                    raise SpendSealError('spend seal is busy') from None
                time.sleep(0.01)

    def advance(self, record: SpendRecord) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self._exclusive():
                merged = _merge(self.read(), record)
                if merged is None:
                    return
                first = self._enroll is not None and not self._path.exists()
                with self._enroll() if first and self._enroll is not None else nullcontext():
                    self._write(merged)
        except OSError as exc:
            raise SpendSealError('spend seal could not be written') from exc
        except SpendSealError:
            raise
        except RuntimeError as exc:
            raise SpendSealError('spend seal could not be recorded in the recovery registry') from exc

    def _write(self, merged: SpendRecord) -> None:
        if sys.platform == 'win32':
            from catchup_core.blobs import private_atomic_bytes  # noqa: PLC0415
            private_atomic_bytes(self._path, json.dumps(
                {'version': 1, 'serial': merged.serial, 'month': merged.month, 'requests': merged.requests,
                 'tokens': merged.tokens, 'keys': sorted(merged.keys)}).encode('utf-8'))
            return
        temporary = self._path.with_name(self._path.name + '.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump({'version': 1, 'serial': merged.serial, 'month': merged.month,
                       'requests': merged.requests, 'tokens': merged.tokens,
                       'keys': sorted(merged.keys)}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._path)


def _digest(catchup_id: str, request_key: str) -> str:
    return hashlib.sha256(json.dumps([catchup_id, request_key]).encode('utf-8')).hexdigest()[:32]


class AdmissionStore:
    def __init__(self, ledger: Ledger, policy: ResourcePolicy, seal: SpendSeal | None = None):
        if not isinstance(policy, ResourcePolicy):
            raise PolicyRequired('explicit namespace resource policy required')
        self.ledger = ledger
        self.policy = policy
        self.seal = seal

    def _sealed(self) -> SpendRecord | None:
        if self.seal is None:
            return None
        try:
            return self.seal.read()
        except SpendSealError:
            raise ResourceLimit('spend seal unreadable; admissions fail closed') from None

    @staticmethod
    def _totals(db: sqlite3.Connection, month: str) -> MonthUsage:
        admitted = db.execute(
            'SELECT count(*),count(u.catchup_id),'
            'coalesce(sum(CASE WHEN u.catchup_id IS NULL THEN a.estimated_tokens ELSE 0 END),0),'
            'coalesce(sum(CASE WHEN u.catchup_id IS NULL THEN 0 ELSE u.input_tokens+u.output_tokens END),0) '
            'FROM resource_admissions a LEFT JOIN resource_usage u '
            'ON u.catchup_id=a.catchup_id AND u.request_key=a.request_key WHERE a.month_utc=?',
            (month,)).fetchone()
        lost = db.execute('SELECT coalesce(sum(requests),0),coalesce(sum(tokens),0) '
                          'FROM resource_uncertain_spend WHERE month_utc=?', (month,)).fetchone()
        return MonthUsage(month, admitted[0] + lost[0], admitted[1], admitted[0] - admitted[1] + lost[0],
                          admitted[3], admitted[2], lost[0], lost[1])

    def month_usage(self, month: str) -> MonthUsage:
        """Counted usage for a UTC month (YYYY-MM). Unreported requests are unknown, counted at their estimate."""
        _identifier(month, 'month')
        self.ledger._require_unlocked()
        self._reconcile_restore(_stamp(datetime.now(UTC)))
        return self._totals(self.ledger.conn, month)

    def _reconcile_restore(self, now: str) -> SpendRecord | None:
        """Count spend that a restored older ledger lost as uncertain; never refund it. Idempotent."""
        sealed = self._sealed()
        if sealed is None:
            return None
        with self.ledger.transaction() as db:
            self._claim(db)
            serial = db.execute('SELECT serial FROM resource_state WHERE singleton=1').fetchone()[0]
            if sealed.serial > serial:
                have = self._totals(db, sealed.month)
                db.execute('INSERT INTO resource_uncertain_spend(month_utc,requests,tokens,reason,recorded_at) '
                           'VALUES(?,?,?,?,?)',
                           (sealed.month, max(0, sealed.requests - have.requests),
                            max(0, sealed.tokens - have.counted_tokens),
                            'ledger older than sealed spend record; lost admissions counted as uncertain', now))
                db.execute('UPDATE resource_state SET serial=?', (sealed.serial,))
        return sealed

    def _seal_spend(self, month: str) -> None:
        if self.seal is None:
            return
        with self.ledger.transaction() as db:
            serial = db.execute('SELECT serial FROM resource_state WHERE singleton=1').fetchone()[0]
            latest = db.execute('SELECT max(month_utc) FROM (SELECT month_utc FROM resource_admissions '
                                'UNION ALL SELECT month_utc FROM resource_uncertain_spend)').fetchone()[0]
            month = max(month, latest) if latest is not None else month
            usage = self._totals(db, month)
            keys = frozenset(_digest(r[0], r[1]) for r in db.execute(
                'SELECT catchup_id,request_key FROM resource_admissions'))
        try:
            self.seal.advance(SpendRecord(serial, month, usage.requests, usage.counted_tokens, keys))
        except SpendSealError:
            raise ResourceLimit('spend seal could not be updated; the reservation stays counted '
                                'but nothing may be disclosed') from None

    def record_usage(self, catchup_id: str, request_key: str, input_tokens: int, output_tokens: int,
                     source: str, now: datetime, zero_reason: str | None = None) -> None:
        """Store provider-reported usage for an admitted request. A receipt never changes; a repeat is a no-op.

        A first receipt of zero input and output tokens needs a stated ``zero_reason`` (it lowers the counted
        spend); the reason is kept with the source.
        """
        _identifier(catchup_id, 'catchup_id')
        _identifier(request_key, 'request_key')
        _identifier(source, 'source')
        _bounded_integer(input_tokens, 'input_tokens', 0)
        _bounded_integer(output_tokens, 'output_tokens', 0)
        stamp = _stamp(now)
        if zero_reason is not None:
            source = f'{source} (zero tokens: {_identifier(zero_reason, "zero_reason")})'
        self._reconcile_restore(stamp)
        with self.ledger.transaction() as db:
            self._claim(db)
            admission = db.execute('SELECT month_utc FROM resource_admissions '
                                   'WHERE catchup_id=? AND request_key=?',
                                   (catchup_id, request_key)).fetchone()
            if admission is None:
                raise ValueError('usage requires an exact admission')
            prior = db.execute('SELECT input_tokens,output_tokens FROM resource_usage '
                               'WHERE catchup_id=? AND request_key=?', (catchup_id, request_key)).fetchone()
            if prior is not None:
                if tuple(prior) != (input_tokens, output_tokens):
                    raise ValueError('a different usage receipt already exists for this request')
            else:
                if input_tokens + output_tokens == 0 and zero_reason is None:
                    raise ValueError('a first usage receipt of zero tokens needs a stated reason')
                db.execute('INSERT INTO resource_usage VALUES(?,?,?,?,?,?)',
                           (catchup_id, request_key, input_tokens, output_tokens, source, stamp))
                db.execute('UPDATE resource_state SET serial=serial+1')
        # A receipt can exceed its estimate. Seal it before acknowledging; exact retries also
        # repair a previous failed seal write without changing the immutable receipt.
        self._seal_spend(admission['month_utc'])

    def uncertain_requests(self, catchup_id: str) -> list[str]:
        """Admitted requests with neither a validated cached output nor a usage receipt: effect is unknown."""
        _identifier(catchup_id, 'catchup_id')
        self.ledger._require_unlocked()
        return [row[0] for row in self.ledger.conn.execute(
            'SELECT a.request_key FROM resource_admissions a WHERE a.catchup_id=? '
            'AND NOT EXISTS(SELECT 1 FROM resource_usage u WHERE u.catchup_id=a.catchup_id '
            'AND u.request_key=a.request_key) '
            'AND NOT EXISTS(SELECT 1 FROM resource_cache c WHERE c.catchup_id=a.catchup_id '
            'AND c.request_key=a.request_key) ORDER BY a.admitted_at,a.rowid', (catchup_id,))]

    def require_active(self, catchup_id: str) -> None:
        """Last check before disclosure: a cancelled Catch-up must not send an already reserved request."""
        _identifier(catchup_id, 'catchup_id')
        self.ledger._require_unlocked()
        self._not_cancelled(self.ledger.conn, catchup_id)

    def _claim(self, db: sqlite3.Connection) -> None:
        self.ledger._require_unlocked()
        db.execute('UPDATE resource_scheduler SET cursor=cursor WHERE singleton=1')

    @staticmethod
    def _not_cancelled(db: sqlite3.Connection, catchup_id: str) -> None:
        if db.execute('SELECT 1 FROM resource_cancellations WHERE catchup_id=?', (catchup_id,)).fetchone():
            raise ResourceLimit('logical Catch-up cancelled')

    def admit(self, catchup_id: str, signal_id: str, request_key: str, prompt_bytes: int,
              now: datetime, *, bundle_id: str | None = None, supplied: bool = False) -> bool:
        """Reserve new requests using a trusted invocation Metric sequence bundle_id.

        None retains the legacy stable Signal group, not a fresh invocation bundle.
        Exact prior requests return False without charging, even in another bundle.
        ``supplied`` marks an answer that already exists for a request key admitted by an earlier Catch-up: it is
        recorded and counted but not refused on a cap, because refusing it would discard paid-for output.
        These are namespace caps only; live provider usage remains unverified.
        """
        _identifier(catchup_id, 'catchup_id')
        _identifier(signal_id, 'signal_id')
        _identifier(request_key, 'request_key')
        bundle_id = signal_id if bundle_id is None else _identifier(bundle_id, 'bundle_id')
        _bounded_integer(prompt_bytes, 'prompt_bytes', 0)
        stamp = _stamp(now)
        sealed = self._reconcile_restore(stamp)
        lost = sealed is not None and _digest(catchup_id, request_key) in sealed.keys
        estimate = self.policy.estimate_tokens(prompt_bytes)
        admitted, month_to_seal = self._reserve(catchup_id, signal_id, request_key, prompt_bytes, bundle_id,
                                                stamp, estimate, sealed, lost, supplied)
        if month_to_seal is not None:
            self._seal_spend(month_to_seal)
        return admitted

    def _reserve(self, catchup_id: str, signal_id: str, request_key: str, prompt_bytes: int, bundle_id: str,
                 stamp: str, estimate: int, sealed: SpendRecord | None, lost: bool,
                 supplied: bool = False) -> tuple[bool, str | None]:
        """One reservation transaction: (admitted, month whose spend must still be sealed)."""
        with self.ledger.transaction() as db:
            self._claim(db)
            self._not_cancelled(db, catchup_id)
            prior = db.execute('SELECT signal_id,prompt_bytes,month_utc FROM resource_admissions '
                               'WHERE catchup_id=? AND request_key=?', (catchup_id, request_key)).fetchone()
            if prior is not None:
                if (prior['signal_id'], prior['prompt_bytes']) != (signal_id, prompt_bytes):
                    raise ValueError('request key already bound to another Signal or prompt size')
                serial = db.execute('SELECT serial FROM resource_state WHERE singleton=1').fetchone()[0]
                behind = self.seal is not None and (sealed is None or sealed.serial < serial)
                # an earlier attempt may have committed without sealing; seal before reporting it as prior
                return False, (prior['month_utc'] if behind else None)
            if lost:
                return False, None      # sent before a restore; its output is lost, the effect stays uncertain
            if not db.execute('SELECT 1 FROM signals WHERE signal_id=?', (signal_id,)).fetchone():
                raise ValueError('unknown Signal')
            paid = supplied and db.execute('SELECT 1 FROM resource_admissions WHERE request_key=?',
                                           (request_key,)).fetchone()
            if not paid:
                self._check_caps(db, catchup_id, signal_id, bundle_id, prompt_bytes, estimate, stamp)
            db.execute('INSERT INTO resource_admissions '
                       '(catchup_id,request_key,signal_id,prompt_bytes,month_utc,admitted_at,bundle_id,'
                       'estimated_tokens) VALUES(?,?,?,?,?,?,?,?)',
                       (catchup_id, request_key, signal_id, prompt_bytes, stamp[:7], stamp, bundle_id, estimate))
            db.execute('UPDATE resource_state SET serial=serial+1')
        return True, stamp[:7]

    def _check_caps(self, db: sqlite3.Connection, catchup_id: str, signal_id: str, bundle_id: str,
                    prompt_bytes: int, estimate: int, stamp: str) -> None:
        policy = self.policy
        attempts = db.execute('SELECT count(DISTINCT catchup_id),coalesce(max(catchup_id=?),0) '
                              'FROM resource_admissions WHERE signal_id=?',
                              (catchup_id, signal_id)).fetchone()
        if not attempts[1] and attempts[0] >= policy.max_attempts_per_signal:
            raise ResourceLimit('namespace per-Signal attempt allocation exhausted')
        count = db.execute('SELECT count(*) FROM resource_admissions WHERE catchup_id=?',
                           (catchup_id,)).fetchone()[0]
        if count >= policy.max_requests_per_catchup:
            raise ResourceLimit('namespace logical catchup allocation exhausted')
        bundle = db.execute('SELECT count(*),coalesce(sum(prompt_bytes),0) FROM resource_admissions '
                            'WHERE catchup_id=? AND bundle_id=?', (catchup_id, bundle_id)).fetchone()
        if bundle[0] >= policy.max_requests_per_bundle:
            raise ResourceLimit('namespace bundle request allocation exhausted')
        if bundle[1] + prompt_bytes > policy.max_prompt_bytes_per_bundle:
            raise ResourceLimit('namespace bundle prompt byte allocation exhausted')
        month = self._totals(db, stamp[:7])
        if month.requests >= policy.max_requests_per_month:
            raise ResourceLimit('namespace UTC month reservation allocation exhausted')
        if (policy.max_tokens_per_month is not None
                and month.counted_tokens + estimate > policy.max_tokens_per_month):
            raise ResourceLimit('namespace UTC month token allocation exhausted (estimates count until '
                                'usage is reported)')

    @staticmethod
    def _aad(catchup_id: str, signal_id: str, request_key: str) -> bytes:
        return json.dumps(['resource-cache-v1', catchup_id, signal_id, request_key],
                          separators=(',', ':')).encode('utf-8')

    def cache_output(self, catchup_id: str, signal_id: str, request_key: str, output: str,
                     now: datetime) -> None:
        _identifier(catchup_id, 'catchup_id')
        _identifier(signal_id, 'signal_id')
        _identifier(request_key, 'request_key')
        if not isinstance(output, str):
            raise ValueError('output must be text')
        stamp = _stamp(now)
        with self.ledger.transaction() as db:
            self._claim(db)
            self._not_cancelled(db, catchup_id)
            reservation = db.execute('SELECT signal_id FROM resource_admissions '
                                     'WHERE catchup_id=? AND request_key=?', (catchup_id, request_key)).fetchone()
            if reservation is None or reservation['signal_id'] != signal_id:
                raise ValueError('output requires an exact Signal/request key admission')
            cipher = self.ledger._cipher
            if cipher is None:
                raise PolicyRequired('resource cache requires the existing ledger cipher')
            aad = self._aad(catchup_id, signal_id, request_key)
            previous = db.execute('SELECT output_encrypted FROM resource_cache '
                                  'WHERE catchup_id=? AND request_key=?', (catchup_id, request_key)).fetchone()
            if previous is not None:
                if cipher.decrypt_json(previous['output_encrypted'], aad) != output:
                    raise ValueError('cached output is immutable for an admitted request key')
                return
            encrypted, version = cipher.encrypt_json(output, aad)
            db.execute('INSERT INTO resource_cache VALUES(?,?,?,?,?,?)',
                       (catchup_id, request_key, signal_id, encrypted, version, stamp))

    def cached_output(self, catchup_id: str, request_key: str) -> str | None:
        _identifier(catchup_id, 'catchup_id')
        _identifier(request_key, 'request_key')
        with self.ledger.transaction() as db:
            self._claim(db)
            self._not_cancelled(db, catchup_id)
            row = db.execute('SELECT c.* FROM resource_cache c JOIN resource_admissions a '
                             'USING(catchup_id,request_key) WHERE c.catchup_id=? AND c.request_key=? '
                             'AND c.signal_id=a.signal_id', (catchup_id, request_key)).fetchone()
            if row is None:
                return None
            cipher = self.ledger._cipher
            if cipher is None:
                raise PolicyRequired('resource cache requires the existing ledger cipher')
            output = cipher.decrypt_json(row['output_encrypted'],
                                         self._aad(catchup_id, row['signal_id'], request_key))
            if not isinstance(output, str):
                raise ValueError('cached output is not text')
            return output

    def cancel(self, catchup_id: str, actor: str) -> None:
        _identifier(catchup_id, 'catchup_id')
        _identifier(actor, 'actor')
        with self.ledger.transaction() as db:
            self._claim(db)
            db.execute('INSERT OR IGNORE INTO resource_cancellations VALUES(?,?)', (catchup_id, actor))

    def is_cancelled(self, catchup_id: str) -> bool:
        _identifier(catchup_id, 'catchup_id')
        self.ledger._require_unlocked()
        return self.ledger.conn.execute('SELECT 1 FROM resource_cancellations WHERE catchup_id=?',
                                        (catchup_id,)).fetchone() is not None

    def select_pending(self, priority_signal_ids: Sequence[str] = (), *,
                       current_catchup_id: str | None = None) -> list[sqlite3.Row]:
        """Select bounded normalized Signals, allowing an admitted Catch-up to resume."""
        if current_catchup_id is not None:
            _identifier(current_catchup_id, 'current_catchup_id')
        if isinstance(priority_signal_ids, (str, bytes)):
            raise ValueError('priority Signal IDs must be a sequence of IDs')
        priority = {_identifier(ident, 'priority Signal ID') for ident in priority_signal_ids}
        selected: list[sqlite3.Row] = []
        with self.ledger.transaction() as db:
            self._claim(db)
            state = db.execute('SELECT * FROM resource_scheduler WHERE singleton=1').fetchone()
            cursor, streak = state['cursor'], state['priority_streak']
            last_scope = (state['last_connector'], state['last_account_scope'])
            for _ in range(self.policy.max_pending_signals):
                excluded = json.dumps([row['signal_id'] for row in selected])
                base = ('SELECT s.*,coalesce(q.last_selected,0) AS resource_last_selected '
                        'FROM signals s LEFT JOIN resource_schedule_signals q USING(signal_id) '
                        "WHERE s.processing_state='normalized' "
                        'AND s.signal_id NOT IN (SELECT value FROM json_each(?)) '
                        'AND ((SELECT count(DISTINCT a.catchup_id) FROM resource_admissions a '
                        'WHERE a.signal_id=s.signal_id)<? OR EXISTS('
                        'SELECT 1 FROM resource_admissions a WHERE a.signal_id=s.signal_id AND a.catchup_id=?)) '
                        'AND (s.signal_id IN (SELECT value FROM json_each(?)))=? ')
                oldest_order = 'ORDER BY resource_last_selected,julianday(s.observed_at),s.rowid LIMIT 1'
                rotating_order = ('ORDER BY (coalesce(q.last_selected,0)>0),'
                                  '(s.connector=? AND s.account_scope=?),resource_last_selected,'
                                  'julianday(s.observed_at),s.rowid LIMIT 1')
                parameters = (excluded, self.policy.max_attempts_per_signal, current_catchup_id,
                              json.dumps(sorted(priority)))
                urgent = db.execute(base + rotating_order, (*parameters, 1, *last_scope)).fetchone()
                force_oldest = streak >= self.policy.max_priority_streak or cursor % (
                    self.policy.max_priority_streak + 1) == 0
                ordinary = db.execute(base + (oldest_order if force_oldest else rotating_order),
                                      (*parameters, 0) if force_oldest else (*parameters, 0, *last_scope)).fetchone()
                use_priority = urgent is not None and (ordinary is None or streak < self.policy.max_priority_streak)
                chosen = urgent if use_priority else ordinary
                if chosen is None:
                    break
                cursor += 1
                streak = min(streak + 1, self.policy.max_priority_streak) if use_priority else 0
                last_scope = (chosen['connector'], chosen['account_scope'])
                db.execute('INSERT INTO resource_schedule_signals VALUES(?,?) '
                           'ON CONFLICT(signal_id) DO UPDATE SET last_selected=excluded.last_selected',
                           (chosen['signal_id'], cursor))
                selected.append(chosen)
            db.execute('UPDATE resource_scheduler SET cursor=?,priority_streak=?,last_connector=?,last_account_scope=? '
                       'WHERE singleton=1', (cursor, streak, *last_scope))
        return selected
