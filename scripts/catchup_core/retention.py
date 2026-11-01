"""Retention review and expiry over registered machine-local artifacts.

Every class period is UNSET unless the owner configures it; nothing is ever deleted by default.
Preview is read-only. Applying an approved preview expires only classes with a proven erasure path:

* ``source``: each content version is expired through the existing deletion journal with a content-keyed
  tombstone, so derived Facts, history, Briefing archives, replay and corpus cases, evidence blobs and
  cached extraction output go with it, identical content cannot return, and an older restore stays locked
  until the sealed deletion checkpoint is satisfied by replaying the tombstone.
* ``replay``: the replay bundle and its case rows, with a retention tombstone replayed on older restores.

Document, corpus, Briefing, history and trace classes stay unsupported (reported, never deleted), and
full checkpoints are never selected. A killed expiry resumes from the journal (``resume_pending_forgets``).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from catchup_core.ledger import Ledger, LedgerError, PermissionDenied

ArtifactClass = Literal["source", "document", "corpus", "replay", "briefing", "trace", "snapshot", "history"]
CLASSES: tuple[ArtifactClass, ...] = (
    "source", "document", "corpus", "replay", "briefing", "trace", "snapshot", "history",
)
APPLICABLE: tuple[ArtifactClass, ...] = ("source", "replay")
EXPIRY_UNAVAILABLE_REASON = "expiry_erasure_path_unavailable"


class RetentionError(ValueError):
    """A retention decision cannot safely be made or applied."""


@dataclass(frozen=True)
class RetentionPolicy:
    periods: tuple[tuple[ArtifactClass, int], ...]
    retain_all: bool = False

    def __post_init__(self) -> None:
        if type(self.retain_all) is not bool or (self.retain_all and self.periods):
            raise RetentionError("retain-all cannot include expiry periods")
        if (not isinstance(self.periods, tuple)
                or any(not isinstance(entry, tuple) or len(entry) != 2 for entry in self.periods)):
            raise RetentionError("invalid retention policy")
        names = [entry[0] for entry in self.periods]
        if (len(names) != len(set(names)) or names != sorted(names)
                or any(name not in CLASSES or type(days) is not int or not 1 <= days <= 3650000
                       for name, days in self.periods)):
            raise RetentionError("invalid retention policy")

    @classmethod
    def from_mapping(cls, raw: object) -> RetentionPolicy:
        if not isinstance(raw, Mapping):
            raise RetentionError("retention policy must map explicit classes to days")
        if raw == {"mode": "retain_all"}:
            return cls((), retain_all=True)
        if any(not isinstance(name, str) or name not in CLASSES for name in raw):
            raise RetentionError("unknown retention policy class")
        entries: list[tuple[ArtifactClass, int]] = []
        for name in CLASSES:
            if name in raw:
                days = raw[name]
                if type(days) is not int or not 1 <= days <= 3650000:
                    raise RetentionError("retention policy days must be positive bounded integers")
                entries.append((name, days))
        return cls(tuple(sorted(entries)))

    @classmethod
    def unset(cls) -> RetentionPolicy:
        """The shipped default: no class has a period, so nothing expires."""
        return cls(())

    @classmethod
    def from_config(cls, policy: object) -> RetentionPolicy:
        """Read the optional ``retention`` mapping of the runtime policy; absent means every period unset."""
        if not isinstance(policy, Mapping) or "retention" not in policy or policy["retention"] is None:
            return cls.unset()
        return cls.from_mapping(policy["retention"])

    def days_for(self, artifact_class: ArtifactClass) -> int | None:
        return dict(self.periods).get(artifact_class)


Decision = Literal["expired", "retained", "protected", "unsupported", "policy_needed"]


@dataclass(frozen=True)
class RetentionEntry:
    artifact_class: ArtifactClass
    token: str
    status: Decision
    reason: str


@dataclass(frozen=True)
class RetentionPreview:
    digest: str
    reference_time: str
    entries: tuple[RetentionEntry, ...]
    coverage: tuple[tuple[ArtifactClass, str], ...]


@dataclass(frozen=True)
class RetentionResult:
    """Counts apply only to the registered current store, not retained checkpoint copies."""

    applied: int
    interrupted: bool = False
    reason: str = "complete"


@dataclass(frozen=True)
class _Candidate:
    entry: RetentionEntry
    identity: tuple[str, ...] = field(repr=False)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    """File identity and content-change stamps; a read may update st_atime, which is not a change."""
    return (info.st_mode, info.st_ino, info.st_dev, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise RetentionError("retention reference time is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        raise RetentionError("retention reference time is invalid") from None


class RetentionPlanner:
    """Plan metadata-only expiry; never discover deletion targets by filesystem glob.

    ``now`` is an explicit review instant, not a retention default. Source periods
    age from the newest registered observation of the entire upstream identity;
    replay periods age from feedback.recorded_at. Other clocks are not inferred.

    Source and replay ages identify review candidates only. Ledger.forget removes
    an upstream identity, not just an expired version; replay deletion has no
    sealed expiry journal or restore anti-resurrection protocol. Neither API is
    an expiry implementation, even under an installation-wide mutation guard.
    """

    def __init__(self, ledger: Ledger, policy: RetentionPolicy, viewer: str, now: datetime, root: Path,
                 *, max_rows: int = 100000, max_bytes: int = 64 * 1024 * 1024):
        if not isinstance(policy, RetentionPolicy) or not isinstance(viewer, str) or not viewer:
            raise RetentionError("invalid retention policy or viewer")
        if not isinstance(now, datetime):
            raise RetentionError("retention reference time is invalid")
        self.now = _instant(now.isoformat())
        if (type(max_rows) is not int or not 1 <= max_rows <= 100000
                or type(max_bytes) is not int or not 1 <= max_bytes <= 1024 * 1024 * 1024):
            raise RetentionError("invalid retention inventory bound")
        self.ledger, self.policy, self.viewer = ledger, policy, viewer
        self.root = Path(root).absolute()
        self.max_rows, self.max_bytes = max_rows, max_bytes

    def _mac(self, value: object) -> str:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return hmac.new(self.ledger._id_key.secret, b"artha.retention.v1\0" + data, hashlib.sha256).hexdigest()

    def _root_check(self) -> None:
        if self.ledger._path.absolute().parent != self.root:
            raise RetentionError("retention root does not own ledger")
        for path in (self.root, *self.root.parents):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise RetentionError("unsafe retention root path")
        ledger_info = self.ledger._path.lstat()
        if not stat.S_ISREG(ledger_info.st_mode) or ledger_info.st_nlink != 1:
            raise RetentionError("unsafe retention ledger path")

    def _file(self, relative: str) -> tuple[object, ...]:
        parts = Path(relative).parts
        if (not parts or Path(relative).is_absolute() or any(part in (".", "..") for part in parts)
                or "\\" in relative):
            raise RetentionError("unsafe retention artifact path")
        path = self.root
        for part in parts[:-1]:
            path /= part
            try:
                info = path.lstat()
            except FileNotFoundError:
                return ("missing",)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise RetentionError("unsafe retention artifact path")
        path /= parts[-1]
        try:
            before = path.lstat()
        except FileNotFoundError:
            return ("missing",)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_dev != self.root.stat().st_dev):
            raise RetentionError("unsafe retention artifact path")
        if before.st_size > self.max_bytes:
            raise RetentionError("retention artifact exceeds byte bound")
        if sys.platform == "win32":
            from catchup_core.quarantine_fs_windows import QuarantineFilesystemError  # noqa: PLC0415
            try:
                return self._native_file(path, before)
            except QuarantineFilesystemError:
                raise RetentionError("retention artifact unavailable or unsafe") from None
        return self._posix_file(path, before)

    def _posix_file(self, path: Path, before: os.stat_result) -> tuple[object, ...]:
        digest = hmac.new(self.ledger._id_key.secret, b"artha.retention.file.v1\0", hashlib.sha256)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if not isinstance(nofollow, int) or not nofollow:
            raise RetentionError("safe no-follow open unavailable")
        descriptor = os.open(path, os.O_RDONLY | nofollow)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if _identity(opened) != _identity(before):
                raise RetentionError("retention artifact path changed")
            header = stream.read(4)
            if not header.startswith(b"AE1"):
                raise RetentionError("retention artifact is not a known encrypted envelope")
            digest.update(header)
            remaining = self.max_bytes - len(header)
            while block := stream.read(min(65536, remaining + 1)):
                remaining -= len(block)
                if remaining < 0:
                    raise RetentionError("retention artifact exceeds byte bound")
                digest.update(block)
            stable = _identity(opened)
            if _identity(os.fstat(stream.fileno())) != stable or _identity(path.lstat()) != stable:
                raise RetentionError("retention artifact path changed")
        return (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns,
                digest.hexdigest())

    def _native_file(self, path: Path, before: os.stat_result) -> tuple[object, ...]:
        from catchup_core.quarantine_fs_windows import open_owner_private_path  # noqa: PLC0415
        digest = hmac.new(self.ledger._id_key.secret, b"artha.retention.file.v1\0", hashlib.sha256)
        with open_owner_private_path(path) as stream:
            opened = stream.snapshot()
            if opened.size > self.max_bytes:
                raise RetentionError("retention artifact exceeds byte bound")
            header = stream.read_chunk(4)
            if not header.startswith(b"AE1"):
                raise RetentionError("retention artifact is not a known encrypted envelope")
            digest.update(header)
            remaining = self.max_bytes - len(header)
            while block := stream.read_chunk(min(65536, remaining + 1)):
                remaining -= len(block)
                if remaining < 0:
                    raise RetentionError("retention artifact exceeds byte bound")
                digest.update(block)
            if (self.max_bytes - remaining != opened.size or stream.snapshot() != opened
                    or _identity(path.lstat()) != _identity(before)):
                raise RetentionError("retention artifact path changed")
        return (opened.identity.volume_serial, opened.identity.file_id.hex(), opened.size,
                opened.last_write_100ns, opened.change_100ns, digest.hexdigest())

    @staticmethod
    def _describe(value: object) -> object:
        """A BLOB is described by its size and digest, so the preview covers it without holding its bytes."""
        if isinstance(value, bytes):
            return {"blob_bytes": len(value), "blob_sha256": hashlib.sha256(value).hexdigest()}
        return value

    def _tables(self) -> dict[str, list[dict[str, Any]]]:
        tables: dict[str, list[dict[str, Any]]] = {}
        names = self.ledger.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchmany(self.max_rows + 1)
        if len(names) > self.max_rows:
            raise RetentionError("retention registry exceeds row bound")
        total_bytes = 0
        for row in names:
            name = str(row[0])
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise RetentionError("unknown retention registry")
            records = self.ledger.conn.execute(f'SELECT * FROM "{name}"').fetchmany(self.max_rows + 1)  # noqa: S608
            if len(records) > self.max_rows:       # the bound applies to each table, so one large table is named
                raise RetentionError(f"retention inventory too large: table {name} exceeds the row bound")
            total_bytes += sum(len(v) for record in records for v in tuple(record) if isinstance(v, bytes))
            values = [{key: self._describe(value) for key, value in dict(record).items()} for record in records]
            try:
                canonical = sorted(values, key=lambda value: json.dumps(value, sort_keys=True))
                total_bytes += len(json.dumps(canonical))
            except (TypeError, ValueError):
                raise RetentionError("unknown retention registry value") from None
            if total_bytes > self.max_bytes:
                raise RetentionError("retention inventory exceeds byte bound")
            tables[name] = canonical
        return tables

    def _allowed(self, signal_ids: tuple[str, ...]) -> bool:
        if not signal_ids:
            return False
        try:
            for signal_id in signal_ids:
                self.ledger.require_signal_operation(signal_id, self.viewer, "view")
        except PermissionDenied:
            return False
        return True

    def _decision(self, klass: ArtifactClass, time: object, protection: str | None,  # noqa: PLR0911
                  unsupported: str | None) -> tuple[Decision, str]:
        if protection:
            return "protected", protection
        days = self.policy.days_for(klass)
        if self.policy.retain_all:
            return "retained", "explicit_retain_all_no_automatic_expiry"
        if days is None:
            return "policy_needed", "explicit_period_required"
        if unsupported:
            return "unsupported", unsupported
        try:
            age = self.now - _instant(time)
        except RetentionError:
            return "unsupported", "registered_reference_time_required"
        if age.total_seconds() < 0:
            return "protected", "future_reference_time"
        if age.days >= days:
            return "expired", "explicit_period_elapsed"
        return "retained", "within_explicit_period"

    def _build(self) -> tuple[RetentionPreview, tuple[_Candidate, ...]]:  # noqa: PLR0912, PLR0915
        self.ledger._require_unlocked()
        self._root_check()
        if self.viewer != self.ledger._owner:
            raise RetentionError("retention requires the current local owner")
        if self.ledger.conn.execute("SELECT 1 FROM deletion_journal WHERE state<>'receipted' LIMIT 1").fetchone():
            raise RetentionError("pending erasure requires authoritative recovery")
        tables = self._tables()
        signals = {str(row["signal_id"]): row for row in tables["signals"]}
        candidates: list[_Candidate] = []
        files: dict[str, tuple[object, ...]] = {}
        inventory_bytes = len(json.dumps(tables))

        def register_file(relative: str) -> None:
            nonlocal inventory_bytes
            if relative in files:
                return
            fingerprint = self._file(relative)
            inventory_bytes += cast(int, fingerprint[2]) if len(fingerprint) > 1 else 0
            if inventory_bytes > self.max_bytes:
                raise RetentionError("retention inventory exceeds byte bound")
            files[relative] = fingerprint

        def add(klass: ArtifactClass, identity: tuple[str, ...], time: object,
                protection: str | None = None, unsupported: str | None = None) -> None:
            status, reason = self._decision(klass, time, protection, unsupported)
            candidates.append(_Candidate(RetentionEntry(klass, self._mac([klass, identity]), status, reason), identity))

        linked = {str(row["signal_id"]) for table in ("fact_evidence", "fact_version_evidence")
                  for row in tables.get(table, [])}
        unresolved = any(row["state"] not in ("done", "not_proceeding") for row in tables["open_items"])
        linked_alerts = {row["alert_id"] for row in tables["alert_facts"]}
        orphan_alerts = any(row["alert_id"] not in linked_alerts for row in tables["alerts"])
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for row in signals.values():
            identity = tuple(str(row[key]) for key in ("connector", "account_scope", "upstream_id"))
            groups.setdefault(identity, []).append(row)
        for identity, versions in sorted(groups.items()):
            signal_ids = tuple(str(row["signal_id"]) for row in versions)
            if not self._allowed(signal_ids):
                continue
            klass: ArtifactClass = "document" if identity[0] == "documents" else "source"
            for row in versions:
                ref = str(row["evidence_blob_ref"])
                if not re.fullmatch(r"evd_[A-Za-z0-9]+", ref):
                    raise RetentionError("unknown retention evidence path")
                register_file(f"blobs/{ref}.blob")
            protection = "supporting_evidence" if linked.intersection(signal_ids) else None
            refs = {row["evidence_blob_ref"] for row in versions}
            markers = {row["content_hmac"] for row in versions}
            if any(row["content_hmac"] in markers and row["signal_id"] not in signal_ids
                   for row in signals.values()):
                protection = "shared_content_marker"
            if any(row["evidence_blob_ref"] in refs and row["signal_id"] not in signal_ids
                   for row in signals.values()):
                protection = "shared_evidence_blob"
            if unresolved:
                protection = "unresolved_commitments"
            if orphan_alerts:
                protection = "deduplication_history"
            if any(row["processing_state"] not in ("excluded", "reconciled") for row in versions):
                protection = "unresolved_interpretation"
            unsupported = None
            if klass == "document" or identity[0] == "capture":
                unsupported = "document_capture_erasure_inventory_required"
            time = max(_instant(row["observed_at"]) for row in versions).isoformat()
            add(klass, identity, time, protection, unsupported)

        feedback = {str(row["feedback_id"]): row for row in tables["feedback"]}
        replay_groups: dict[str, list[str]] = {}
        for row in tables["replay_cases"]:
            replay_groups.setdefault(str(row["feedback_id"]), []).append(str(row["signal_id"]))
        for replay_id, replay_source_ids in sorted(replay_groups.items()):
            if not self._allowed(tuple(replay_source_ids)):
                continue
            if not re.fullmatch(r"fbk_[A-Za-z0-9]+", replay_id):
                raise RetentionError("unknown retention replay path")
            relative = f"replay/{replay_id}.ae1"
            register_file(relative)
            replay_feedback = feedback.get(replay_id)
            add("replay", (replay_id,), replay_feedback["recorded_at"] if replay_feedback else None)

        for row in tables.get("corpus_cases", []):
            if not self._allowed((str(row["signal_id"]),)):
                continue
            case_id = str(row["case_id"])
            if not re.fullmatch(r"case_[A-Za-z0-9_-]{1,64}", case_id):
                raise RetentionError("unknown retention corpus path")
            relative = f"corpus/{case_id}.ae1"
            register_file(relative)
            add("corpus", (case_id,), None, unsupported="bounded_corpus_erasure_api_required")

        for row in tables.get("briefings", []):
            briefing_source_ids = tuple(str(ref["signal_id"]) for ref in tables.get("briefing_signals", [])
                                        if ref["catchup_id"] == row["catchup_id"])
            if self._allowed(briefing_source_ids):
                add("briefing", (str(row["catchup_id"]), str(row["version"])), row["published_at"],
                    unsupported="versioned_archive_registry_and_prune_api_required")
        for row in tables.get("fact_versions", []):
            try:
                self.ledger.require_fact_operation(str(row["fact_id"]), self.viewer, "view")
            except PermissionDenied:
                continue
            add("history", (str(row["fact_id"]), str(row["recorded_at"])), row["recorded_at"],
                unsupported="history_erasure_and_restore_contract_required")

        coverage: tuple[tuple[ArtifactClass, str], ...] = tuple((klass,
            "retained_by_policy" if self.policy.retain_all and klass != "snapshot" else {
            "source": "applicable", "document": "unsupported",
            "corpus": "unsupported", "replay": "applicable",
            "briefing": "unsupported", "trace": "unsupported", "snapshot": "protected",
            "history": "unsupported",
        }[klass] if self.policy.days_for(klass) is not None or klass == "snapshot" else "policy_needed")
            for klass in CLASSES)
        ordered = tuple(sorted(candidates, key=lambda candidate: (
            candidate.entry.artifact_class, candidate.entry.token)))
        reference = self.now.isoformat()
        digest = self._mac({"policy": self.policy.periods, "retain_all": self.policy.retain_all,
                            "time": reference, "viewer": self.viewer,
                            "root": str(self.root), "key_version": self.ledger._id_key.version,
                            "bounds": [self.max_rows, self.max_bytes], "registry": tables, "files": files,
                            "decisions": [entry.entry.__dict__ for entry in ordered], "coverage": coverage,
                            "access_policy": [self.ledger._owner, sorted(self.ledger._consent_subjects),
                                              sorted(self.ledger._known_subjects or ())]})
        return RetentionPreview(digest, reference, tuple(candidate.entry for candidate in ordered), coverage), ordered

    def _review(self) -> tuple[RetentionPreview, tuple[_Candidate, ...]]:
        try:
            connection = self.ledger.conn
            if connection.in_transaction:
                return self._build()
            connection.execute("BEGIN")
            try:
                return self._build()
            finally:
                connection.execute("ROLLBACK")
        except RetentionError:
            raise
        except (OSError, LedgerError, sqlite3.Error, ValueError, TypeError, KeyError):
            raise RetentionError("retention preview unavailable") from None

    def preview(self) -> RetentionPreview:
        return self._review()[0]

    def _require_erasure_paths(self, candidates: tuple[_Candidate, ...]) -> None:
        """Refuse before any effect when a dependent artifact has no registered deleter."""
        ledger = self.ledger
        for candidate in candidates:
            if candidate.entry.status != "expired":
                continue
            if candidate.entry.artifact_class == "replay":
                if ledger._replay_deleter is None:
                    raise RetentionError(EXPIRY_UNAVAILABLE_REASON)
                continue
            if ledger._blob_deleter is None:
                raise RetentionError(EXPIRY_UNAVAILABLE_REASON)
            connector, scope, upstream = candidate.identity
            signals = [row[0] for row in ledger.conn.execute(
                "SELECT signal_id FROM signals WHERE connector=? AND account_scope=? AND upstream_id=?",
                (connector, scope, upstream))]
            for signal_id in signals:
                if ledger.conn.execute("SELECT 1 FROM briefing_signals WHERE signal_id=? UNION ALL "
                                       "SELECT 1 FROM briefing_facts bf JOIN fact_evidence fe "
                                       "ON bf.fact_id=fe.fact_id WHERE fe.signal_id=?",
                                       (signal_id, signal_id)).fetchone() and ledger._artifact_deleter is None:
                    raise RetentionError(EXPIRY_UNAVAILABLE_REASON)
                if ledger.conn.execute("SELECT 1 FROM replay_cases WHERE signal_id=?",
                                       (signal_id,)).fetchone() and ledger._replay_deleter is None:
                    raise RetentionError(EXPIRY_UNAVAILABLE_REASON)

    def _expire(self, candidate: _Candidate) -> None:
        if candidate.entry.artifact_class == "replay":
            self.ledger.expire_replay(candidate.identity[0])
            return
        connector, scope, upstream = candidate.identity
        for (marker,) in self.ledger.conn.execute(
                "SELECT DISTINCT content_hmac FROM signals WHERE connector=? AND account_scope=? "
                "AND upstream_id=? ORDER BY content_hmac", (connector, scope, upstream)).fetchall():
            self.ledger.expire_content(marker)

    def apply(self, preview: RetentionPreview, approved_digest: str, *,
              mutation_guard: Callable[[], AbstractContextManager[None]] | None = None) -> RetentionResult:
        """Expire exactly the reviewed ``expired`` source and replay entries; everything else is untouched.

        The approval must match a fresh review, taken again inside the parent mutation guard. A failure
        part-way returns an interrupted result without private detail: the deletion journal resumes through
        ``Ledger.resume_pending_forgets`` and a fresh review is required afterwards. Counts cover the
        registered current store only; retained checkpoint copies are not touched and an older restore
        stays locked until the sealed deletion checkpoint is satisfied.
        """
        if (not isinstance(preview, RetentionPreview) or not isinstance(approved_digest, str)
                or not hmac.compare_digest(preview.digest, approved_digest)):
            raise RetentionError("retention approval does not match preview")
        if mutation_guard is None:
            raise RetentionError("retention application requires parent mutation guard")
        current, candidates = self._review()
        if current != preview:
            raise RetentionError("retention preview changed; fresh review required")
        self._require_erasure_paths(candidates)
        applied = 0
        try:
            with mutation_guard():
                current, candidates = self._review()
                if current != preview:
                    raise RetentionError("retention preview changed; fresh review required")
                self._require_erasure_paths(candidates)
                for candidate in candidates:
                    if candidate.entry.status == "expired" and candidate.entry.artifact_class in APPLICABLE:
                        self._expire(candidate)
                        applied += 1
        except RetentionError:
            raise
        except Exception:
            return RetentionResult(applied, True, "application_interrupted_fresh_review_required")
        return RetentionResult(applied, reason="complete" if applied else "no_applicable_expiry")


def check_anti_resurrection(
    ledger: Ledger,
    connector: str,
    account_scope: str,
    upstream_id: str,
    content_hmac: str | None = None,
) -> bool:
    """Anti-resurrection check across source and derived artifacts (Row 5 / RA-10 / RA-16).

    Verifies whether an entity was tombstoned, forgotten, or expired by retention.
    Returns True if the item is permitted (not tombstoned/expired).
    Returns False if the item is suppressed by an anti-resurrection tombstone or purge.
    """
    if ledger.is_tombstoned(connector, account_scope, upstream_id, content_hmac):
        return False
    if content_hmac:
        row = ledger.conn.execute(
            "SELECT 1 FROM tombstones WHERE content_hmac=? LIMIT 1", (content_hmac,)
        ).fetchone()
        if row:
            return False
    return True


def audit_anti_resurrection(
    ledger: Ledger,
    *,
    candidate_records: Sequence[Mapping[str, Any]] = (),
    cache_entries: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[str], list[str]]:
    """Audit source and cache entries against anti-resurrection tombstones.

    Returns (suppressed_upstream_ids, violation_details).
    """
    suppressed: list[str] = []
    violations: list[str] = []
    for rec in candidate_records:
        connector = str(rec.get("connector", ""))
        scope = str(rec.get("account_scope", ""))
        upstream = str(rec.get("upstream_id", ""))
        chmac = rec.get("content_hmac")
        chmac_str = str(chmac) if chmac else None
        if upstream and not check_anti_resurrection(ledger, connector, scope, upstream, chmac_str):
            suppressed.append(upstream)
            violations.append(f"candidate source record {connector}/{scope}/{upstream} matches active tombstone")
    for entry in cache_entries:
        connector = str(entry.get("connector", ""))
        scope = str(entry.get("account_scope", ""))
        upstream = str(entry.get("upstream_id", ""))
        chmac = entry.get("content_hmac")
        chmac_str = str(chmac) if chmac else None
        if upstream and not check_anti_resurrection(ledger, connector, scope, upstream, chmac_str):
            suppressed.append(upstream)
            violations.append(f"cache entry {connector}/{scope}/{upstream} matches active tombstone")
    return suppressed, violations
