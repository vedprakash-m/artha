"""Exact documentary field review receipts, independent of user-asserted Facts."""
from __future__ import annotations

import hmac
import json
import math
from argparse import Namespace
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from catchup_core.coordinator import Deps

from catchup_core import ids
from catchup_core.contracts import FactStatus, Provenance
from catchup_core.ledger import Ledger, LedgerError, PermissionDenied
from catchup_core.policy import authorize_source

TOKEN_TTL_SECONDS = 1800


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate typed value member")
        result[name] = value
    return result


def _value(text: str) -> Any:
    if not isinstance(text, str) or not text or len(text.encode("utf-8")) > 200_000:
        raise ValueError("typed_value_json requires bounded JSON text")
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
        _json(value)
    except (ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("typed_value_json requires finite unambiguous JSON") from exc
    return value


@dataclass(frozen=True)
class FieldBinding:
    subject_id: str
    domain: str
    predicate: str
    typed_value_json: str
    signal_id: str
    signal_version: str
    span: tuple[int, int]
    document_id: str
    page: int
    provenance_hmac: str
    fact_id: str | None = None
    fact_version: str | None = None

    def __post_init__(self) -> None:
        for name in ("subject_id", "domain", "predicate", "signal_id", "signal_version", "document_id",
                     "provenance_hmac"):
            value = getattr(self, name)
            if (not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 1024
                    or any(ord(char) < 32 for char in value)):
                raise ValueError(f"{name} requires a bounded resolved identity")
        if (type(self.span) is not tuple or len(self.span) != 2
                or any(type(offset) is not int for offset in self.span)
                or not 0 <= self.span[0] < self.span[1]):
            raise ValueError("span requires two increasing nonnegative UTF-8 byte offsets")
        if type(self.page) is not int or self.page < 1:
            raise ValueError("page requires a positive integer")
        if (self.fact_id is None) != (self.fact_version is None):
            raise ValueError("fact_id and fact_version must be provided together")
        for value in (self.fact_id, self.fact_version):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 1024):
                raise ValueError("Fact identity and version require bounded text")
        object.__setattr__(self, "typed_value_json", _json(_value(self.typed_value_json)))


class FieldReviewStore:
    def __init__(self, ledger: Ledger, viewer: str, now: Callable[[], datetime], *,
                 source_text: Callable[[str], str]):
        if ledger._cipher is None:
            raise LedgerError("document field review requires encryption")
        self.ledger = ledger
        self.viewer = viewer
        self.now = now
        self.source_text = source_text

    def _current(self, binding: FieldBinding) -> str:
        led = self.ledger
        if self.viewer != led._owner:
            raise PermissionDenied("only the configured owner may review documentary fields")
        if not led.check_restore():
            raise PermissionDenied("privacy history must be current before documentary review")
        led._require_unlocked()
        source = led.signal_row(binding.signal_id)
        if (source is None or source["connector"] != "documents"
                or source["upstream_id"] != f"{binding.document_id}:p{binding.page}"
                or source["upstream_version"] != binding.signal_version
                or source["content_hmac"] != binding.provenance_hmac
                or source["span_unit"] != "utf8-byte" or not source["evidence_blob_ref"]
                or source["processing_state"] == "excluded" or not led.source_head(binding.signal_id)):
            raise LedgerError("documentary source identity, version or presence changed")
        for operation in ("view", "correct"):
            led.require_signal_operation(binding.signal_id, self.viewer, operation)
        row = led.conn.execute("SELECT subjects FROM signal_access WHERE signal_id=?",
                               (binding.signal_id,)).fetchone()
        subjects = json.loads(row[0]) if row else []
        if (not isinstance(subjects, list) or not subjects
                or any(not isinstance(subject, str) or not subject for subject in subjects)
                or binding.subject_id not in subjects):
            raise PermissionDenied("field subject is not resolved in source provenance")
        scope_subjects = set(subjects)
        if binding.fact_id is not None:
            fact = led.require_fact_operation(binding.fact_id, self.viewer, "correct")
            if (fact.domain != binding.domain or fact.predicate != binding.predicate
                    or fact.primary_subject_id != binding.subject_id or fact.recorded_at != binding.fact_version
                    or fact.status not in ("current", "unknown")
                    or _json(fact.value) != binding.typed_value_json
                    or (binding.signal_id, *binding.span) not in fact.evidence):
                raise LedgerError("Fact identity, version, value or documentary evidence changed")
            scope_subjects.update(str(row[0]) for row in led.conn.execute(
                "SELECT subject_id FROM fact_subjects WHERE fact_id=?", (binding.fact_id,)))
        for scope in (binding.domain, f"{source['connector']}:{source['account_scope']}"):
            authorize_source(led, sorted(scope_subjects), led._consent_subjects, scope, self.viewer,
                             operations=("view", "correct"))
        return ids.content_hmac(led._id_key, _json(sorted(scope_subjects)))

    def _text(self, binding: FieldBinding) -> str:
        self._current(binding)
        text = self.source_text(binding.signal_id)
        if not isinstance(text, str) or len(text.encode("utf-8")) > 2_000_000:
            raise ValueError("trusted source reader did not return bounded original text")
        if not hmac.compare_digest(ids.content_hmac(self.ledger._id_key, text), binding.provenance_hmac):
            raise ValueError("trusted original text differs from the Signal content")
        raw = text.encode("utf-8")
        start, end = binding.span
        if end > len(raw):
            raise ValueError("field span exceeds the original text")
        try:
            raw[:start].decode("utf-8")
            return raw[start:end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("field span must use UTF-8 byte boundaries") from exc

    def _identity(self, binding: FieldBinding) -> str:
        return ids.content_hmac(self.ledger._id_key, "document-field-v1:" + _json(asdict(binding)))

    def _stamp(self) -> int:
        instant = self.now()
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("review clock requires an explicit instant")
        if not math.isfinite(instant.timestamp()):
            raise ValueError("review clock requires a finite instant")
        return int(instant.timestamp())

    def _signature(self, identity: str, stamp: int, generation: int, revision: int, scope: str) -> str:
        return ids.content_hmac(self.ledger._id_key, _json(
            ["document-field-review-v1", identity, self.viewer, stamp, generation, revision, scope]))

    def _requirement(self, binding: FieldBinding) -> tuple[str, int]:
        identity = self._identity(binding)
        row = self.ledger.conn.execute("SELECT * FROM document_field_requirements WHERE binding_id=?",
                                       (identity,)).fetchone()
        if row is None:
            raise ValueError("document field needs a current preview")
        cipher = self.ledger._cipher
        if cipher is None:
            raise LedgerError("document field review requires encryption")
        stored = cipher.decrypt_json(row["payload"], aad=identity.encode())
        if (_json(stored) != _json(asdict(binding)) or row["signal_id"] != binding.signal_id
                or row["fact_id"] != binding.fact_id):
            raise LedgerError("document field requirement differs from its exact binding")
        return identity, int(row["revision"])

    def _confirmation(self, identity: str, revision: int) -> dict[str, Any] | None:
        row = self.ledger.conn.execute("SELECT * FROM document_field_confirmations WHERE binding_id=?",
                                       (identity,)).fetchone()
        if row is None:
            return None
        cipher = self.ledger._cipher
        if cipher is None:
            raise LedgerError("document field review requires encryption")
        payload = cipher.decrypt_json(row["payload"], aad=(identity + ":confirmation").encode())
        if (not isinstance(payload, dict) or payload.get("binding_id") != identity
                or payload.get("state") != row["state"] or payload.get("revision") != revision
                or payload.get("actor") != self.viewer or payload.get("signal_id") != row["signal_id"]):
            raise LedgerError("document field receipt metadata differs from its encrypted record")
        return payload

    def preview(self, binding: FieldBinding) -> dict[str, Any]:
        excerpt = self._text(binding)
        led = self.ledger
        with led.transaction() as db:
            scope = self._current(binding)
            identity = self._identity(binding)
            cipher = led._cipher
            if cipher is None:
                raise LedgerError("document field review requires encryption")
            payload, version = cipher.encrypt_json(asdict(binding), aad=identity.encode())
            db.execute("INSERT OR IGNORE INTO document_field_requirements"
                       "(binding_id,signal_id,fact_id,payload,key_version) VALUES(?,?,?,?,?)",
                       (identity, binding.signal_id, binding.fact_id, payload, version))
            identity, revision = self._requirement(binding)
            confirmation = self._confirmation(identity, revision)
            generation, stamp = led._permission_generation(), self._stamp()
            token = f"{stamp}:{revision}:{self._signature(identity, stamp, generation, revision, scope)}"
            return {"binding": asdict(binding), "excerpt": excerpt, "apply_token": token,
                    "permission_generation": generation, "expires_in_seconds": TOKEN_TTL_SECONDS,
                    "confirmed": bool(confirmation and confirmation["state"] == "confirmed"
                                      and confirmation["scope"] == scope)}

    def confirm(self, binding: FieldBinding, token: str) -> dict[str, Any]:
        return self._change(binding, token, "confirmed")

    def withdraw(self, binding: FieldBinding, token: str) -> dict[str, Any]:
        return self._change(binding, token, "withdrawn")

    def _change(self, binding: FieldBinding, token: str, state: str) -> dict[str, Any]:
        self._text(binding)
        led = self.ledger
        with led.transaction() as db:
            scope = self._current(binding)
            identity, revision = self._requirement(binding)
            generation, now = led._permission_generation(), self._stamp()
            try:
                if not isinstance(token, str) or len(token) > 256:
                    raise ValueError
                stamp_text, revision_text, signature = token.split(":")
                stamp, reviewed_revision = int(stamp_text), int(revision_text)
                if (str(stamp) != stamp_text or str(reviewed_revision) != revision_text
                        or not 0 <= now - stamp <= TOKEN_TTL_SECONDS):
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid or expired documentary field preview") from exc
            confirmation = self._confirmation(identity, revision)
            receipt = ids.content_hmac(led._id_key, "document-field-receipt-v1:" + token)
            replay = bool(confirmation and confirmation["receipt"] == receipt
                          and confirmation["state"] == state and confirmation["scope"] == scope
                          and confirmation["permission_generation"] == generation)
            if not replay and (reviewed_revision != revision or not hmac.compare_digest(
                    signature, self._signature(identity, stamp, generation, revision, scope))):
                raise ValueError("documentary field or permissions changed; review again")
            if replay and confirmation is not None:
                return self._receipt(confirmation)
            payload = {"binding_id": identity, "signal_id": binding.signal_id, "actor": self.viewer,
                       "receipt": receipt, "state": state, "revision": revision + 1, "scope": scope,
                       "permission_generation": generation, "recorded_at": self.now().isoformat()}
            cipher = led._cipher
            if cipher is None:
                raise LedgerError("document field review requires encryption")
            encrypted, version = cipher.encrypt_json(payload, aad=(identity + ":confirmation").encode())
            db.execute("UPDATE document_field_requirements SET revision=revision+1 WHERE binding_id=?", (identity,))
            db.execute("INSERT INTO document_field_confirmations VALUES(?,?,?,?,?) ON CONFLICT(binding_id) "
                       "DO UPDATE SET state=excluded.state,payload=excluded.payload,key_version=excluded.key_version",
                       (identity, binding.signal_id, state, encrypted, version))
            if binding.fact_id is not None:
                self._link(binding, identity, state)
            return self._receipt(payload)

    def _link(self, binding: FieldBinding, identity: str, state: str) -> None:
        from catchup_core.fact_access import document_link_proof  # noqa: PLC0415
        db = self.ledger.conn
        fact = db.execute('SELECT * FROM facts WHERE fact_id=?', (binding.fact_id,)).fetchone()
        if fact is None or fact['provenance_class'] == 'user_confirmed':
            return
        db.execute('INSERT OR IGNORE INTO document_field_facts VALUES(?)', (binding.fact_id,))
        # A confirmed link already makes the Fact read as current/verified (fact_access.document_fact_state), so a
        # confirmation never rewrites the stored row.  Withdrawing a stored 'current' Fact is the one stored change,
        # and it goes through the ledger so the previous state stays in fact_versions.
        if state != 'confirmed' and fact['status'] == 'current' and binding.fact_id is not None:
            self.ledger.set_fact_status(binding.fact_id, FactStatus.UNKNOWN, Provenance.MODEL_HYPOTHESIS)
        source = db.execute('SELECT subjects FROM signal_access WHERE signal_id=?',
                            (binding.signal_id,)).fetchone()
        subjects = set(json.loads(source[0]))
        subjects.add(binding.subject_id)
        subjects.update(str(row[0]) for row in db.execute('SELECT subject_id FROM fact_subjects WHERE fact_id=?',
                                                         (binding.fact_id,)))
        link = {'fact_id': binding.fact_id, 'binding_id': identity, 'signal_id': binding.signal_id,
                'fact_version': binding.fact_version, 'subject_id': binding.subject_id,
                'domain': binding.domain, 'predicate': binding.predicate, 'signal_version': binding.signal_version,
                'provenance_hmac': binding.provenance_hmac, 'span_start': binding.span[0], 'span_end': binding.span[1],
                'scope': _json(sorted(subjects)), 'reviewer': self.viewer}
        proof = document_link_proof(db, link)
        db.execute('INSERT OR REPLACE INTO document_field_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (*link.values(), proof))

    @staticmethod
    def _receipt(payload: dict[str, Any]) -> dict[str, Any]:
        return {name: payload[name] for name in ("binding_id", "receipt", "state", "recorded_at")}

    def verified(self, binding: FieldBinding) -> bool:
        try:
            self._text(binding)
            db = self.ledger.conn
            owns_snapshot = not db.in_transaction
            if owns_snapshot:
                db.execute("BEGIN")
            try:
                scope = self._current(binding)
                identity, revision = self._requirement(binding)
                confirmation = self._confirmation(identity, revision)
                return bool(confirmation and confirmation["state"] == "confirmed" and confirmation["scope"] == scope)
            finally:
                if owns_snapshot:
                    db.execute("ROLLBACK")
        except Exception:
            return False


def field_command(args: Namespace, deps: Deps) -> dict[str, Any]:
    deps.validate_inputs()
    if deps.viewer != deps.ledger._owner:
        raise PermissionDenied('only the configured owner may review documentary fields')
    if deps.mode.value == 'shadow':
        raise PermissionDenied('documentary review requires an owner session, not shadow inspection')
    led = deps.ledger
    fact = led.require_fact_operation(args.fact_id, deps.viewer, 'correct')
    bindings = []
    for signal_id, start, end in fact.evidence:
        source = led.signal_row(signal_id)
        if source is None or source['connector'] != 'documents' or not led.source_head(signal_id):
            continue
        document, separator, page = source['upstream_id'].rpartition(':p')
        if not separator or not page.isdecimal():
            raise LedgerError('document source has no page identity')
        bindings.append(FieldBinding(fact.primary_subject_id, fact.domain, fact.predicate, _json(fact.value),
            signal_id, source['upstream_version'], (start, end), document, int(page), source['content_hmac'],
            fact.fact_id, fact.recorded_at))
    if len(bindings) != 1:
        raise LedgerError('field review requires exactly one documentary field evidence span')
    binding = bindings[0]

    def source_text(signal_id: str) -> str:
        led.require_signal_operation(signal_id, deps.viewer, 'view')
        source = led.signal_row(signal_id)
        if source is None:
            raise LedgerError('documentary source is missing')
        return deps.blobs.get(source['evidence_blob_ref'])

    review = FieldReviewStore(led, deps.viewer, deps.now, source_text=source_text)
    token = getattr(args, 'apply', None)
    if token is None:
        return review.preview(binding)
    return review.withdraw(binding, token) if getattr(args, 'reopen', False) else review.confirm(binding, token)
