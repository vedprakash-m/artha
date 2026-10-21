"""Ingest bundles: the hand-run multi-machine data path (re-artha §6.10, §7.9, §12.13).

A secondary machine (for example the Windows host that owns a Connector) runs an export and writes an
**immutable, signed, encrypted** bundle into the sync folder.  The authority host imports bundles at its next
Catch-up.  There is no listener and no replication process (PULL over PUSH, §1.4); a bundle carries records only,
never State File edits or approvals.

Design rules enforced here:

* the file is ``{"header": …, "mac": …, "body": …}``; the MAC covers the canonical header and the ciphertext, the
  body is authenticated-encrypted with a key derived from the same enrolled producer key, so a forged, truncated or
  edited bundle fails before anything is decoded;
* the header binds producer, namespace, recipient, account scope, authority generation, sequence, part count,
  requested and completed window and cursor generation; the importer re-checks all of them against *its own*
  configuration (a validly signed bundle is still untrusted input);
* a Work bundle is never imported by the personal importer, however well signed (encryption is not permission);
* import is idempotent: an immutable acknowledgment file records each ``(producer, scope, sequence)``;
* a missing part, a sequence gap, a stale authority generation or a crash before acknowledgment leaves the scope
  visibly **incomplete**, and the Briefing says how old the secondary machine's data is;
* a bundle addressed to another importer (other recipient, scope or namespace, or an unenrolled producer) is left
  untouched and listed as skipped; a malformed file, a bad signature or a stale generation is moved to ``quarantine/``
  and never retried silently.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from catchup_core.backup import atomic_bytes
from catchup_core.contracts import CoverageStatus, FailureClass
from catchup_core.coordinator import ConnectorFetch, ConnectorRecord
from catchup_core.crypto import DecryptionError, EnvelopeCipher, StaticKeyProvider

__all__ = ["BUNDLE_SCHEMA", "BundleError", "ImportResult", "ProducerKey", "enroll_producer", "export_bundle",
           "export_fetch", "import_bundles", "load_producer_key", "next_sequence"]

BUNDLE_SCHEMA = "artha.bundle.v1"
MAX_BUNDLE_BYTES = 8 * 1024 * 1024          # ciphertext size limit
MAX_RECORDS = 5000
MAX_RECORD_CHARS = 200_000
_HEADER_FIELDS = ("schema", "bundle_id", "producer", "namespace", "recipient", "account_scope", "authority_generation",
                  "sequence", "part", "parts", "requested_since", "completed_until", "cursor_generation",
                  "record_count", "created_at")


class BundleError(ValueError):
    """A bundle that must not be imported (the reason is bounded and safe to display)."""


class NotAddressedHere(BundleError):
    """A well-formed bundle meant for another importer sharing the folder: left in place, never moved or deleted.

    Moving it would destroy another host's data (a Work importer's, or a second personal host's) merely because this
    importer must not read it.  Only files that are *ours* and invalid are quarantined.
    """


@dataclass(frozen=True)
class ProducerKey:
    """One enrolled producer's secret (32 bytes), held in the OS credential store on both machines."""
    producer: str
    secret: bytes

    def __post_init__(self) -> None:
        if len(self.secret) != 32:
            raise ValueError("producer keys are 32 bytes")

    def _derive(self, label: bytes) -> bytes:
        return hmac.new(self.secret, b"artha.bundle.v1|" + label, hashlib.sha256).digest()

    def cipher(self) -> EnvelopeCipher:
        return EnvelopeCipher(StaticKeyProvider({1: self._derive(b"encrypt")}))

    def sign(self, header: Mapping[str, Any], body: str) -> str:
        message = _canonical(header) + b"|" + hashlib.sha256(body.encode()).digest()
        return hmac.new(self._derive(b"sign"), message, hashlib.sha256).hexdigest()


def _canonical(header: Mapping[str, Any]) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode()


def _file_name(header: Mapping[str, Any]) -> str:
    safe = "".join(c for c in str(header["producer"]) if c.isalnum() or c in "-_")
    return f"bundle-{safe}-{int(header['sequence']):08d}-p{int(header['part']):03d}.json"


def _ack_name(producer: str, scope: str, sequence: int) -> str:
    digest = hashlib.sha256(f"{producer}|{scope}".encode()).hexdigest()[:12]
    return f"ack-{digest}-{sequence:08d}.json"


def export_bundle(
    directory: Path, key: ProducerKey, *, namespace: str, recipient: str, account_scope: str,
    authority_generation: int, sequence: int, records: Sequence[ConnectorRecord],
    requested_since: str, completed_until: str, cursor_generation: int = 1, created_at: datetime,
    part: int = 1, parts: int = 1, bundle_id: str = "",
) -> Path:
    """Write one immutable part.  An existing file is never overwritten (a re-export needs a new sequence)."""
    if len(records) > MAX_RECORDS:
        raise BundleError("too many records for one part; split the export into parts")
    body_obj = [{"upstream_id": r.upstream_id, "text": r.text, "provider_version": r.provider_version,
                 "occurred_at": r.occurred_at, "incomplete": r.incomplete} for r in records]
    if any(len(r.text) > MAX_RECORD_CHARS for r in records):
        raise BundleError("a record exceeds the size limit")
    header = {
        "schema": BUNDLE_SCHEMA, "bundle_id": bundle_id or f"{key.producer}-{sequence}", "producer": key.producer,
        "namespace": namespace, "recipient": recipient, "account_scope": account_scope,
        "authority_generation": authority_generation, "sequence": sequence, "part": part, "parts": parts,
        "requested_since": requested_since, "completed_until": completed_until,
        "cursor_generation": cursor_generation, "record_count": len(body_obj),
        "created_at": created_at.isoformat(),
    }
    aad = _canonical(header)
    envelope, _ = key.cipher().encrypt(json.dumps(body_obj).encode(), aad=aad)
    body = base64.b64encode(envelope).decode()
    path = Path(directory) / _file_name(header)
    if path.exists():
        raise BundleError("bundle part already exists; published bundles are immutable")
    atomic_bytes(path, json.dumps({"header": header, "mac": key.sign(header, body), "body": body}).encode())
    return path


@dataclass
class ImportResult:
    fetch: ConnectorFetch
    imported: list[str] = field(default_factory=list)
    quarantined: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    newest_data_at: str | None = None


def _quarantine(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def _load(path: Path, keys: Mapping[str, ProducerKey]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise BundleError("bundle exceeds the size limit")
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        header, mac, body = wrapper["header"], wrapper["mac"], wrapper["body"]
        producer = str(header["producer"])
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise BundleError("bundle is not readable") from exc
    key = keys.get(producer)
    if key is None:
        raise NotAddressedHere("producer is not enrolled here")
    if not hmac.compare_digest(key.sign(header, str(body)), str(mac)):
        raise BundleError("signature does not verify")
    if set(header) != set(_HEADER_FIELDS) or header["schema"] != BUNDLE_SCHEMA:
        raise BundleError("unsupported bundle schema")
    try:
        records = json.loads(key.cipher().decrypt(base64.b64decode(str(body)), aad=_canonical(header)))
    except (DecryptionError, ValueError) as exc:
        raise BundleError("body cannot be decrypted") from exc
    if not isinstance(records, list) or len(records) != header["record_count"]:
        raise BundleError("record count does not match the header")
    return header, records


def _check_binding(header: Mapping[str, Any], *, namespace: str, recipient: str, account_scope: str,
                   authority_generation: int) -> None:
    if header["namespace"] != namespace:
        raise NotAddressedHere("namespace mismatch: this importer does not accept that data class")
    if header["recipient"] != recipient:
        raise NotAddressedHere("bundle is addressed to another recipient")
    if header["account_scope"] != account_scope:
        raise NotAddressedHere("bundle is for another account scope")
    if int(header["authority_generation"]) < authority_generation:
        raise BundleError("bundle belongs to a former authority generation")


def import_bundles(
    directory: Path, keys: Mapping[str, ProducerKey], *, name: str, namespace: str, recipient: str,
    account_scope: str, authority_generation: int, now: datetime, required: bool = True,
    max_age_hours: float = 48.0, move: Callable[[Path, Path], None] | None = None,
) -> ImportResult:
    """Import every verified, complete, not-yet-acknowledged sequence; report exactly what is missing."""
    directory = Path(directory)
    quarantine = directory / "quarantine"
    mover = move or _quarantine
    parts: dict[tuple[str, int], dict[int, tuple[Path, dict[str, Any], list[dict[str, Any]]]]] = {}
    rejected: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    for path in sorted(directory.glob("bundle-*.json")) if directory.is_dir() else []:
        try:
            header, records = _load(path, keys)
            _check_binding(header, namespace=namespace, recipient=recipient, account_scope=account_scope,
                           authority_generation=authority_generation)
        except NotAddressedHere as exc:
            skipped.append((path.name, str(exc)))
            continue
        except BundleError as exc:
            rejected.append((path.name, str(exc)))
            mover(path, quarantine / path.name)
            continue
        parts.setdefault((str(header["producer"]), int(header["sequence"])), {})[int(header["part"])] = (
            path, header, records)

    blind: list[str] = [f"{n}: {why}" for n, why in rejected]
    imported: list[str] = []
    records_out: list[ConnectorRecord] = []
    newest: str | None = None
    by_producer: dict[str, list[int]] = {}
    for (producer, sequence), got in sorted(parts.items()):
        by_producer.setdefault(producer, []).append(sequence)
        sample = next(iter(got.values()))[1]
        ack = directory / _ack_name(producer, account_scope, sequence)
        if ack.exists():
            continue                                         # idempotent: already imported
        total = int(sample["parts"])
        if set(got) != set(range(1, total + 1)):
            blind.append(f"{producer} #{sequence}: {len(got)} of {total} parts present")
            continue
        for _path, _header, recs in got.values():
            records_out.extend(ConnectorRecord(str(r["upstream_id"]), str(r["text"]), r.get("provider_version"),
                                               r.get("occurred_at"), bool(r.get("incomplete"))) for r in recs)
        completed = str(sample["completed_until"])
        newest = max(newest, completed) if newest else completed
        imported.append(f"{producer}#{sequence}")
        atomic_bytes(ack, json.dumps({"producer": producer, "scope": account_scope, "sequence": sequence,
                                      "completed_until": completed, "imported_at": now.isoformat()}).encode())
    blind.extend(_sequence_gaps(directory, by_producer, account_scope))

    latest = newest or _latest_ack(directory, account_scope)
    if latest is None:
        blind.append("no bundle has ever been imported for this scope")
        status = CoverageStatus.UNAVAILABLE
    else:
        age_hours = (now - datetime.fromisoformat(latest.replace("Z", "+00:00"))).total_seconds() / 3600
        if age_hours > max_age_hours:
            blind.append(f"secondary machine data is {int(age_hours)}h old (limit {int(max_age_hours)}h)")
        status = CoverageStatus.DEGRADED if blind else CoverageStatus.COMPLETE
    fetch = ConnectorFetch(name, account_scope, tuple(records_out), status,
                           FailureClass.PROVIDER if status is CoverageStatus.UNAVAILABLE else None,
                           tuple(blind), required)
    return ImportResult(fetch, imported, rejected, skipped, latest)


def _sequence_gaps(directory: Path, by_producer: Mapping[str, Sequence[int]], account_scope: str) -> list[str]:
    """A missing sequence number means an export never arrived; say so instead of importing around it."""
    notes: list[str] = []
    for producer, seqs in by_producer.items():
        acked: list[int] = []
        for path in directory.glob("ack-*.json"):
            try:
                data = json.loads(path.read_text())
            except (ValueError, OSError):
                continue
            if data.get("producer") == producer and data.get("scope") == account_scope:
                acked.append(int(data["sequence"]))
        everything = sorted(set(seqs) | set(acked))
        missing = sorted(set(range(everything[0], everything[-1] + 1)) - set(everything)) if everything else []
        if missing:
            notes.append(f"{producer}: sequence gap, missing {missing[:5]}")
    return notes


def _latest_ack(directory: Path, account_scope: str) -> str | None:
    stamps = []
    for path in directory.glob("ack-*.json") if directory.is_dir() else []:
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if data.get("scope") == account_scope:
            stamps.append(str(data["completed_until"]))
    return max(stamps) if stamps else None


def _service(producer: str) -> str:
    return f"artha-bundle-{producer}"


def enroll_producer(producer: str, backend: Any, secret_b64: str | None = None) -> str:
    """Create (or import) a producer secret in the OS credential store; returns it for one-time manual copy.

    Enrollment is an explicit, user-run step on *both* machines: the same secret is imported on the other host.
    Nothing is distributed over the network, and an importer without the secret quarantines the producer's files.
    """
    import secrets  # noqa: PLC0415
    secret = base64.b64decode(secret_b64, validate=True) if secret_b64 else secrets.token_bytes(32)
    ProducerKey(producer, secret)                              # validates the length
    encoded = base64.b64encode(secret).decode()
    backend.set_password(_service(producer), "secret", encoded)
    if backend.get_password(_service(producer), "secret") != encoded:
        raise BundleError("the credential store did not persist the producer secret")
    return encoded


def load_producer_key(producer: str, backend: Any) -> ProducerKey | None:
    raw = backend.get_password(_service(producer), "secret")
    if not isinstance(raw, str):
        return None
    try:
        return ProducerKey(producer, base64.b64decode(raw, validate=True))
    except ValueError:
        return None


def next_sequence(directory: Path, producer: str) -> int:
    safe = "".join(c for c in producer if c.isalnum() or c in "-_")
    found = [int(p.name.split("-")[-2]) for p in Path(directory).glob(f"bundle-{safe}-*-p*.json")] \
        if Path(directory).is_dir() else []
    return max(found, default=0) + 1


def export_fetch(directory: Path, key: ProducerKey, fetch: ConnectorFetch, *, namespace: str, recipient: str,
                 authority_generation: int, since: str, now: datetime) -> Path:
    """Publish a finished fetch.  Only a COMPLETE fetch may claim a completed window; anything less is refused,
    because a bundle that looked complete would turn a secondary-machine gap into apparent reassurance."""
    if fetch.status is not CoverageStatus.COMPLETE:
        raise BundleError(f"fetch is {fetch.status.value}; an incomplete export is not published")
    return export_bundle(directory, key, namespace=namespace, recipient=recipient, account_scope=fetch.account_scope,
                         authority_generation=authority_generation, sequence=next_sequence(directory, key.producer),
                         records=fetch.records, requested_since=since, completed_until=now.isoformat(), created_at=now)
