"""Shared synthetic helpers for ``tests/catchup_core/`` (eliminates cross-test imports)."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import struct
import subprocess
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import yaml
from catchup_core import checkpoint as ck
from catchup_core import coordinator as co
from catchup_core import crypto, domain_pack as dp, ids, ledger as lg, policy, runtime
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import CatchupRequest, CoverageStatus, ExecutionMode
from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider, StaticKeyProvider
from catchup_core.ids import HmacKey
from catchup_core.ledger import Ledger
from catchup_core.recovery_access import RecoveryAccess
from catchup_core.renewal_inputs import SourceFile
from catchup_core.renewal_store import RenewalError, digest


class FakeKeyring:
    """In-memory synthetic keyring backend for core runtime and renewal tests."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.store[(service, account)] = value


class Keyring(FakeKeyring):
    """FakeKeyring extended with delete support."""

    def delete_password(self, service: str, account: str) -> None:
        self.store.pop((service, account), None)


class InMemorySealedKeyring:
    """Simple ``values`` dict-backed synthetic keyring for ``KeyringSealedStore``."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account, value: str) -> None:
        self.values[(service, account)] = value


class SQLiteKeyring:
    """Synthetic, process-shared SQLite keyring stand-in."""

    def __init__(self, path: str | Path) -> None:
        self.path = path
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS records"
                "(service TEXT,account TEXT,value TEXT,PRIMARY KEY(service,account))"
            )

    def get_password(self, service: str, account: str) -> str | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT value FROM records WHERE service=? AND account=?", (service, account)
            ).fetchone()
            return row[0] if row else None

    def set_password(self, service: str, account: str, value: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?)", (service, account, value))

    def delete_password(self, service: str, account: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM records WHERE service=? AND account=?", (service, account))


def write_cfg(root: Path, **over) -> None:
    """Write a synthetic ``config/catchup_core.yaml`` into ``root``."""
    (root / "config").mkdir(exist_ok=True)
    data = {
        "sources": {
            "school_email": {"connector": "gmail", "account_scope": "personal", "kwargs": {"query": "label:school"}},
            "calendar": {"connector": "google_calendar", "account_scope": "family"},
        },
        "subjects": ["kid1", "kid2", "adult1"],
        "consent_required": ["adult1"],
        "viewer": "ved",
    }
    data.update(over)
    (root / "config" / "catchup_core.yaml").write_text(yaml.safe_dump(data))


# ── Shared coordinator / regression constants & builders ───────────────────
CORE_KEY = ids.HmacKey(1, b"i" * 32)
COORDINATOR_CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
REGRESSION_CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"7" * 32}))
RESTORE_CIPHER = EnvelopeCipher(StaticKeyProvider({1: b"r" * 32}))
STREAM_CIPHER = EnvelopeCipher(StaticKeyProvider({1: b"s" * 32}))
CORE_NOW = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)
CORE_PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs" / "kids_school.yaml")
CORE_MSG = "Families: SAT registration closes October 14. Please register online."
CORE_QUOTE = "registration closes October 14"

MODEL_IDENTITY = {
    "provider": "synthetic",
    "model": "offline-extractor",
    "revision": "fixture-1",
    "retention_policy": "synthetic-no-retention",
}


def model_for(text_to_value: dict[str, tuple[str, str, str]]):
    """Deterministic extractor stand-in matching exact quotes inside ``<untrusted>`` blocks."""

    def run(prompt: str) -> str:
        message = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
        items = []
        for quote, (pred, value, subject) in text_to_value.items():
            if quote in message:
                idx = message.index(quote)
                items.append({
                    "predicate": pred,
                    "value": value,
                    "subject_id": subject,
                    "quote": quote,
                    "span": [idx, idx + len(quote)],
                })
        return json.dumps({"complete": True, "candidates": items})

    return run


CORE_EXTRACT = model_for({CORE_QUOTE: ("exam_registration_deadline", "2026-10-14", "kid1")})


def coord_req(cid: str = "cu_1", mode: ExecutionMode = ExecutionMode.AUTHORITATIVE, **kw) -> CatchupRequest:
    return CatchupRequest(cid, "ved", "cli", mode=mode, host="mac", **kw)


def coord_email(
    records=(co.ConnectorRecord("m1", CORE_MSG),),
    status: CoverageStatus = CoverageStatus.COMPLETE,
    error=None,
):
    dated = tuple(replace(record, occurred_at=record.occurred_at or CORE_NOW.isoformat()) for record in records)
    return lambda: co.ConnectorFetch("school_email", "acct", dated, status, error)


def coord_cal(status: CoverageStatus = CoverageStatus.COMPLETE, error=None, records=()):
    return lambda: co.ConnectorFetch("calendar", "acct", tuple(records), status, error)


def build_coordinator_world(tmp_path: Path):
    """Generator matching ``test_coordinator.world`` without cross-test imports."""
    ledger = lg.Ledger(tmp_path / "l.db", id_key=CORE_KEY, cipher=COORDINATOR_CIPHER, owner_id="ved").open()
    ledger.migrate()
    reviewed = {"enabled": True}
    upsert_signal = ledger.upsert_signal

    def upsert_reviewed_signal(signal):
        signal_id, created = upsert_signal(signal)
        if reviewed["enabled"]:
            row = ledger.signal_row(signal_id)
            if not ledger._has_personal_binding(
                "signal", signal_id, row["upstream_version"], row["connector"],
                row["account_scope"], row["content_hmac"],
            ):
                with ledger.transaction() as db:
                    ledger._record_personal_binding(
                        db, "signal", signal_id, row["upstream_version"], row["connector"],
                        row["account_scope"], row["content_hmac"], provenance="source_review",
                    )
        return signal_id, created

    ledger.upsert_signal = upsert_reviewed_signal
    blobs = EncryptedBlobStore(tmp_path / "blobs", COORDINATOR_CIPHER, CORE_KEY)
    published: list[tuple[str, int]] = []

    def make(fetchers, extractor=CORE_EXTRACT, source_reviewed=True, **kw):
        reviewed["enabled"] = source_reviewed
        return co.Deps(
            ledger=ledger,
            pack=CORE_PACK,
            blobs=blobs,
            id_key=CORE_KEY,
            fetchers=fetchers,
            extractor=extractor,
            subjects=["kid1", "kid2"],
            viewer="ved",
            now=lambda: CORE_NOW,
            source_class={"school_email": "school_email", "calendar": "calendar"},
            publisher=lambda cid, text, v: published.append((cid, v)),
            coverage_line=lambda: "Coverage: test.",
            **kw,
        )

    yield ledger, blobs, make, published
    ledger.close()


def regression_extract(prompt: str) -> str:
    text = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
    for value in ("2026-10-14", "2026-10-16"):
        if value in text:
            idx = text.index(value)
            return json.dumps({
                "complete": True,
                "candidates": [{
                    "predicate": "exam_registration_deadline",
                    "subject_id": "kid1",
                    "value": value,
                    "quote": value,
                    "span": [idx, idx + 10],
                }],
            })
    return '{"complete": true, "candidates": []}'


def build_regression_world(tmp_path: Path):
    """Generator matching ``test_release_regressions.world``."""
    led = lg.Ledger(tmp_path / "ledger.db", id_key=CORE_KEY, cipher=REGRESSION_CIPHER).open()
    led.migrate()
    blobs = EncryptedBlobStore(tmp_path / "blobs", REGRESSION_CIPHER, CORE_KEY)
    texts: list[str] = []

    def deps(records=()):
        return co.Deps(
            ledger=led,
            pack=CORE_PACK,
            blobs=blobs,
            id_key=CORE_KEY,
            fetchers={
                "school_email": lambda: co.ConnectorFetch("school_email", "acct", tuple(records)),
                "calendar": lambda: co.ConnectorFetch("calendar", "acct"),
            },
            extractor=regression_extract,
            subjects=["kid1"],
            viewer="owner",
            now=lambda: CORE_NOW,
            source_class={"school_email": "school_email", "calendar": "calendar"},
            publisher=lambda cid, text, version: texts.append(text),
        )

    yield led, blobs, deps, texts
    led.close()


def regression_request(cid: str = "cu_a", **kw) -> CatchupRequest:
    return CatchupRequest(cid, "owner", "cli", host="test-host", **kw)


def raw_email_rec(i: int, **kw):
    r = {"id": f"m{i}", "source": "gmail", "date_iso": "2026-10-11", "subject": f"Subject {i}", "body": "body text"}
    r.update(kw)
    return r


def raw_cal_rec(i: int, **kw):
    r = {
        "id": f"e{i}",
        "source": "google_calendar",
        "summary": "Soccer",
        "start": "2026-10-14T15:30:00-07:00",
        "end": "2026-10-14T16:30:00-07:00",
        "location": "Field 2",
        "all_day": False,
    }
    r.update(kw)
    return r


def adapter_gen(records, scopes=(), exc=None):
    from lib.connector_coverage import report_scope

    def fetch(**_kw):
        for scope_name, status, kw in scopes:
            report_scope(scope_name, status, **kw)
        yield from records
        if exc:
            raise exc

    return fetch


def build_triage_pack(holidays=()):
    data = yaml.safe_load((Path(dp.__file__).parent / "packs" / "kids_school.yaml").read_text())
    data["bindings"]["school_calendar"] = {"holidays": list(holidays)}
    return dp.load_pack(data)


def triage_fresh(hours: int = 1):
    return {"school_email": CORE_NOW - timedelta(hours=hours), "calendar": CORE_NOW - timedelta(hours=hours)}


def triage_conn_of(ledger):
    def _c(sid):
        return ledger.conn.execute("SELECT connector FROM signals WHERE signal_id=?", (sid,)).fetchone()[0]

    return _c


# ── Release / cutover / scorecard helpers ──────────────────────────────────
CUTOVER_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
CUTOVER_ESCROW = {
    "age-key/artha": b"AGE-SECRET-KEY-1SYNTHETIC",
    "artha-ledger/key.1": b"k" * 32,
    "artha-ledger-ids/key.1": b"i" * 32,
}


def release_scorecard(config=None):
    cohort = policy.evaluation_cohort(config or {"model_identity": MODEL_IDENTITY}, ["kids_school"])
    cases = [
        {"case_id": "live", "severity": "P1", "expected": ["a"], "surfaced": ["a"]},
        {"case_id": "p2", "severity": "P2", "expected": ["b"], "surfaced": ["b"]},
        {"case_id": "negative", "case_type": "negative", "source_negative_reviewed": True, "expected": [], "surfaced": []},
        *[{"case_id": f"cross{i}", "case_type": "cross_domain", "expected": [f"cross{i}"], "surfaced": [f"cross{i}"]}
          for i in range(2)],
        {"case_id": "goal", "case_type": "goal_correction", "before": 2, "after": 1, "expected": ["corrected"], "surfaced": ["corrected"]},
    ]
    for case in cases:
        case.update(
            domain="kids_school",
            signal_id=f"sig_{case['case_id']}",
            consented_live=True,
            reviewed=True,
            cohort=cohort,
            catchup_id="cu_synthetic",
            split="held_out",
            obligation_ids=list(case["expected"]),
            thread_ids=[case["case_id"]],
            as_of="2026-10-12T00:00:00Z",
            latest_evidence_at="2026-10-11T00:00:00Z",
            corpus_id="synthetic-release-corpus",
        )
    result = {
        "schema": "artha.release.v4",
        "cohort": cohort,
        "cases": len(cases),
        "live_cases": len(cases),
        "owner_signed_at": "reviewed",
        "evaluations": cases,
        "bounds": {
            "kids_school": {
                "minimum_precision": 0.8,
                "minimum_recall": 1,
                "max_source_age_hours": 48,
                "source_precedence": {name: list(pred.authority) for name, pred in CORE_PACK.predicates.items()},
                "normal_workload": {"signals": 20},
                "maximum_review_minutes": 15,
                "maximum_p95_seconds": 180,
                "maximum_monthly_usd": 50,
            }
        },
        "measurements": {
            "kids_school": {
                "cohort": cohort,
                "review_minutes": 5,
                "p95_seconds": 100,
                "monthly_usd": 10,
                "workload": {"signals": 20},
                "source_age_hours": {"school_email": 1, "calendar": 1},
            }
        },
    }
    result["corpus"] = {
        "schema": "artha.release-corpus.v1",
        "held_out": True,
        "independently_labeled": True,
        "development_obligation_ids": ["development-obligation"],
        "development_thread_ids": ["development-thread"],
        "development_end_at": "2026-10-01T00:00:00Z",
        "id": "synthetic-release-corpus",
        "cohort": cohort,
    }
    result["document_fields"] = {name: {"correct": 100, "total": 100} for name in ("dates", "amounts", "names")}
    result["document_fields"]["false_confirmations"] = {"false": 0, "total": 200}
    result["document_fields"].update(cohort=cohort, corpus_id="synthetic-release-corpus")
    measurement = result["measurements"]["kids_school"]
    measurement.update(completed_runs=20, total_runs=20)
    measurement["workloads"] = {}
    for name in ("normal_cold", "normal_warm", "long_gap", "provider_reset", "retention_1y", "retention_2y", "digest_limited"):
        measurement["workloads"][name] = {
            "cohort": cohort,
            "runs": 5,
            "passed": True,
            "p95_seconds": 1,
            "reference_machine": "synthetic-reference",
            "workload": {"signals": 20},
            "fair_progress": True,
            "incomplete_interpretation_safe": True,
            "verified_alerts_preserved": True,
            "coverage_degradation_reported": True,
            "query_verified": True,
            "restore_verified": True,
            "plaintext_fallback": False,
            "fetch_skipped": True,
            "vault_skipped": True,
        }
        if name == "long_gap":
            measurement["workloads"][name]["workload"] = {"signals": 200, "gap_days": 30}
        elif name == "provider_reset":
            measurement["workloads"][name]["workload"] = {"signals": 200, "provider_reset": True}
        elif name in ("retention_1y", "retention_2y"):
            measurement["workloads"][name]["workload"] = {"signals": 3650, "retention_years": 1 if name == "retention_1y" else 2}
    return result


def build_preservation_checkpoint(
    tmp_path: Path,
    cipher: EnvelopeCipher,
    *,
    include_state: bool = True,
    escrow: dict[str, bytes] | None = None,
    name_prefix: str = "ck",
) -> Path:
    src = tmp_path / f"{name_prefix}_src"
    repo_dir = src / "repo"
    local_root = src / "local"
    local_dir = local_root / "catchup_core"
    state_dir = repo_dir / "state"
    local_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        local_root.chmod(0o700)
    local_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        local_dir.chmod(0o700)
    state_dir.mkdir(parents=True, exist_ok=True)
    with Ledger(local_dir / "ledger.db", id_key=CORE_KEY, cipher=cipher) as ledger:
        ledger.migrate()
    if include_state:
        (state_dir / "open_items.md").write_text("- id: OI-1\n  status: open\n  description: Synthetic\n")
    (repo_dir / "config").mkdir()
    (repo_dir / "config/catchup_core.yaml").write_text("cutover: false\n")
    roots = {"local": local_root, "repo": repo_dir}
    version = cipher._provider.current_version()
    return ck.create(
        "personal",
        roots,
        tmp_path / f"{name_prefix}_out",
        cipher,
        {**CUTOVER_ESCROW, f"artha-ledger/key.{version}": cipher._provider.key(version), "artha-ledger-ids/key.1": CORE_KEY.secret}
        if escrow is None
        else escrow,
        "synthetic-passphrase-1234",
        now=CUTOVER_NOW,
        lock_path=tmp_path / f"{name_prefix}.lock",
        context={
            "preservation": {
                "schema": "artha.installation-preservation.v1",
                "repository_root": "repo",
                "runtime_root": "local",
                "root_names": ["local", "repo"],
                "inventory": "recursive-fail-closed",
            }
        },
    )


def synthetic_recovery_acceptance(tmp_path: Path, personal: Path, cipher: EnvelopeCipher, *, cohort=None):
    work_root = tmp_path / "synthetic-work"
    work_root.mkdir()
    (work_root / "work-state.md").write_text("Synthetic Work state, never cloud-copied")
    work_cipher = EnvelopeCipher(StaticKeyProvider({1: b"w" * 32}))
    work = ck.create(
        "work",
        {"work": work_root},
        tmp_path / "synthetic-work-checkpoints",
        work_cipher,
        {"age-key/artha": CUTOVER_ESCROW["age-key/artha"], "artha-work-checkpoint/key.1": b"w" * 32},
        "separate-synthetic-work-passphrase",
        now=CUTOVER_NOW,
        lock_path=tmp_path / "synthetic-work.lock",
    )
    work_manifest = ck.verify(work, work_cipher)
    personal_generation = ck.verify(personal, cipher)["generation"]
    personal_digest = hashlib.sha256(personal.read_bytes()).hexdigest()
    work_digest = hashlib.sha256(work.read_bytes()).hexdigest()
    stamp = "2026-10-25T00:00:00+00:00"

    def observation(profile, digest_hex, generation, os_name=None):
        value = {
            "profile": profile,
            "checkpoint_sha256": digest_hex,
            "checkpoint_generation": generation,
            "observed_at": stamp,
            "owner_reviewed_at": stamp,
            "outcome": "success",
            "privacy": {
                "current_history_verified": True,
                "replay_verified": True,
                "history_sha256": hashlib.sha256(f"synthetic-current-{profile}-privacy".encode()).hexdigest(),
            },
        }
        value.update({"os": os_name, "native": True} if os_name else {"source": "onedrive_web", "recovered_sha256": digest_hex})
        return value

    return {
        "schema": "artha.recovery-rehearsal.v2",
        "passed": True,
        "reconciled": True,
        "owner_signed_at": stamp,
        "checkpoint_sha256": personal_digest,
        "checkpoint_generation": personal_generation,
        "cohort": cohort or {"implementation": "synthetic-only"},
        "work_checkpoint": {"path": str(work), "sha256": work_digest, "profile": "work", "generation": work_manifest["generation"]},
        "observations": {
            "mac_personal": observation("personal", personal_digest, personal_generation, "macos"),
            "windows_work": observation("work", work_digest, work_manifest["generation"], "windows"),
            "personal_cloud_copy": observation("personal", personal_digest, personal_generation),
        },
    }


def build_cutover_configured(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    (repo / "state" / "open_items.md").write_text("- id: OI-1\n  status: open\n  description: Synthetic\n")
    (repo / "config").mkdir()
    cohort = policy.evaluation_cohort

    def fixture_cohort(config, domains, **kwargs):
        kwargs.setdefault("workspace", repo)
        return cohort(config, domains, **kwargs)

    monkeypatch.setattr(policy, "evaluation_cohort", fixture_cohort)
    keyring = FakeKeyring()
    cipher = EnvelopeCipher(KeyringKeyProvider("artha-ledger", backend=keyring))
    rollback = build_preservation_checkpoint(tmp_path, cipher)
    independent_dir = tmp_path / "independent_copy"
    independent_dir.mkdir()
    independent = independent_dir / rollback.name
    independent.write_bytes(rollback.read_bytes())

    def artifact(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return approved(path)

    def approved(path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    rollback_record = approved(rollback)
    config = {
        "cutover": True,
        "viewer": "owner",
        "subjects": ["kid1"],
        "model_identity": MODEL_IDENTITY,
        "sources": {
            "school_email": {"connector": "gmail", "account_scope": "personal"},
            "calendar": {"connector": "google_calendar", "account_scope": "family"},
        },
        "release": {
            "authority_host": "authority",
            "domains": ["kids_school"],
            "owner_approved_at": "reviewed",
            "stage1_clearance": artifact("clearance.json", {"passed": True, "owner_signed_at": "reviewed"}),
            "scorecard": artifact("scorecard.json", release_scorecard()),
            "rollback_snapshot": rollback_record,
            "independent_copy": {
                **approved(independent),
                "failure_domain": "cloud",
                "owner_verified_at": CUTOVER_NOW.isoformat(),
                "recovered_sha256": rollback_record["sha256"],
            },
            "recovery_rehearsal": None,
        },
    }
    config["release"]["scorecard"] = artifact("scorecard.json", release_scorecard(config))
    config["release"]["recovery_rehearsal"] = artifact(
        "recovery_rehearsal.json",
        synthetic_recovery_acceptance(tmp_path, rollback, cipher, cohort=policy.evaluation_cohort(config, ["kids_school"])),
    )
    monkeypatch.setattr(policy.socket, "gethostname", lambda: "authority")
    request = CatchupRequest("cu_fenced", "owner", "cli", host="authority")

    def build(*, inspection=False, mutation=False, safety_control=False, environ=None, req_override=None):
        (repo / "config" / "catchup_core.yaml").write_text(yaml.safe_dump(config))
        return runtime.build_deps(
            req_override or request,
            repo_root=repo,
            environ={"ARTHA_LOCAL_DIR": str(tmp_path / "private"), **(environ or {})},
            keyring_backend=keyring,
            handler_loader=lambda _: None,
            auth_loader=lambda _: {},
            inspection=inspection,
            mutation=mutation,
            safety_control=safety_control,
        )

    return config, request, keyring, build


# ── Restore / quarantine helpers ───────────────────────────────────────────
RESTORE_GENERATION = b"ck_restore_guard"
RECOVERY_NOW = datetime(2026, 10, 25, tzinfo=UTC)


def restore_container(path: Path, entries=None, *, profile: str = "personal") -> Path:
    entries = (
        entries
        if entries is not None
        else [("repo/a.txt", b"authorized baseline", "file", 0o600), ("repo/z.txt", b"last baseline", "file", 0o600)]
    )
    recorded = []
    with path.open("wb") as handle:
        handle.write(b"ACK2" + struct.pack(">H", len(RESTORE_GENERATION)) + RESTORE_GENERATION + struct.pack(">I", 0))
        writer = ck._Writer(handle, RESTORE_CIPHER, RESTORE_GENERATION.decode())
        for name, body, kind, mode in entries:
            header = {"path": name, "kind": kind, "mode": mode}
            ending = {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            writer.frame("h", json.dumps(header).encode())
            writer.frame("d", body)
            writer.frame("e", json.dumps(ending).encode())
            recorded.append({**header, **ending, "source_sha256": ending["sha256"]})
        writer.frame(
            "m",
            json.dumps({
                "schema": "artha.checkpoint.v2",
                "generation": RESTORE_GENERATION.decode(),
                "profile": profile,
                "escrow_sha256": hashlib.sha256(b"").hexdigest(),
                "entries": recorded,
                "excluded": {},
            }).encode(),
        )
        writer.frame("z", json.dumps({"members": len(recorded)}).encode())
    return path


def restore_run(path: Path, dest: Path, **kwargs):
    return ck.restore(path, dest, RESTORE_CIPHER, [], replay_privacy=kwargs.pop("replay_privacy", lambda _: None), **kwargs)


def scan_spy(monkeypatch, *, on_scan=lambda _sink, _result: None):
    original = ck._scan
    released = []

    def observed(source, cipher, sink=None, opened=None, **kwargs):
        def output(header, body):
            released.append(body)
            sink(header, body)

        result = original(source, cipher, output if sink else None, opened, **kwargs)
        on_scan(sink, result)
        return result

    monkeypatch.setattr(ck, "_scan", observed)
    return released


class TrustedSealedStore:
    def __init__(self) -> None:
        self.checkpoint = None
        self.history = {"tombstones": [], "consent": []}
        self.read_hook = None
        self.replay_hook = None

    def read(self):
        if self.read_hook:
            self.read_hook()
        return self.checkpoint

    def write(self, checkpoint) -> None:
        self.checkpoint = checkpoint

    def replay(self):
        result = copy.deepcopy(self.history)
        if self.replay_hook:
            self.replay_hook()
        return result

    def refresh(self, ledger) -> None:
        ledger.seal_privacy()
        self.history = ledger.privacy_history()


class NoNative:
    def get_password(self, service, account):
        raise AssertionError("no native key access authorized")

    def set_password(self, *args):
        raise AssertionError("no key enrollment authorized")

    def delete_password(self, *args):
        raise AssertionError("no native mutation authorized")


def build_recovery_setup(tmp_path: Path):
    """Generator matching ``test_recovery_access.setup``."""

    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    config = {
        "viewer": "owner",
        "subjects": ["owner", "adult"],
        "consent_required": ["adult"],
        "sources": {"mail": {"connector": "school_email", "account_scope": "test"}},
    }
    (repo / "config/catchup_core.yaml").write_text(yaml.safe_dump(config))
    sealed = TrustedSealedStore()
    ledger = Ledger(
        tmp_path / "source/catchup_core/ledger.db",
        id_key=HmacKey(1, bytes([81]) * 32),
        cipher=RESTORE_CIPHER,
        sealed=sealed,
        owner_id="owner",
        clock=lambda: RECOVERY_NOW.isoformat(),
    ).open()
    ledger.migrate()
    sealed.refresh(ledger)
    access = RecoveryAccess(repo, NoNative(), RESTORE_CIPHER, sealed_for=lambda mode: sealed, now=lambda: RECOVERY_NOW)
    result = SimpleNamespace(
        repo=repo,
        config=config,
        sealed=sealed,
        ledger=ledger,
        access=access,
        dest=tmp_path / "restored",
        root=tmp_path,
        ledger_name="local/catchup_core/ledger.db",
    )
    yield result
    ledger.close()


def recovery_checkpoint(setup, *, extra=(), profile: str = "personal", name: str = "original.ack") -> Path:
    from catchup_core.backup import database_bytes

    return restore_container(
        setup.root / name,
        [
            (setup.ledger_name, database_bytes(setup.ledger._path), "sqlite", 0o600),
            ("repo/notes.txt", b"SYNTHETIC-PRIVATE-NOTES", "file", 0o600),
            *extra,
        ],
        profile=profile,
    )


# ── Work stream & renewal helpers ──────────────────────────────────────────
STREAM_GENERATION = "ck_synthetic_streams"
STREAM_ORDINARY = b"ordinary synthetic bytes"
STREAM_OWNER = "work_state/note.txt"
STREAM_NOW = datetime(2026, 11, 6, tzinfo=UTC)


def stream_identity(owner: str, owner_kind: str, name: str) -> dict[str, str]:
    return {"kind": "stream", "owner": owner, "owner_kind": owner_kind, "name": name, "stream_type": "$DATA"}


def stream_ending(data: bytes) -> dict[str, object]:
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def stream_key(identity: dict[str, str]) -> str:
    return json.dumps(
        [identity["owner"], identity["owner_kind"], identity["name"], identity["stream_type"]],
        separators=(",", ":"),
        ensure_ascii=True,
    )


def stream_checkpoint(
    path: Path,
    *,
    streams=None,
    magic: bytes = b"ACK3",
    schema: str = "artha.checkpoint.v3",
    profile: str = "work",
    member_path: str = STREAM_OWNER,
    edit_manifest=lambda _manifest: None,
    edit_terminator=lambda _terminator: None,
    cipher=STREAM_CIPHER,
):
    streams = (
        streams
        if streams is not None
        else [
            (stream_identity(member_path, "file", "synthetic secret name"), b"synthetic secret stream bytes"),
            (stream_identity(member_path, "file", "empty"), b""),
            (stream_identity("work_state/folder", "directory", "directory stream"), b"folder bytes"),
            (stream_identity("work_state", "directory", "root stream"), b"root bytes"),
        ]
    )
    member = {"path": member_path, "kind": "file", "mode": 0o600}
    member_entry = {**member, **stream_ending(STREAM_ORDINARY), "source_sha256": stream_ending(STREAM_ORDINARY)["sha256"]}
    stream_entries = [{**identity, **stream_ending(body)} for identity, body in streams]
    binding = {
        "schema": "artha.work-checkpoint-binding.v2",
        "profile": "work",
        "plaintext_allowed": False,
        "identity": "a" * 64,
        "generation": 1,
        "head_sha256": "b" * 64,
        "mac": "c" * 64,
        "members": {member_path: stream_ending(STREAM_ORDINARY)},
        "streams": {stream_key(identity): stream_ending(body) for identity, body in streams},
    }
    manifest = {
        "schema": schema,
        "generation": STREAM_GENERATION,
        "profile": profile,
        "escrow_sha256": hashlib.sha256(b"").hexdigest(),
        "entries": [member_entry],
        "streams": stream_entries,
        "roots": ["work_state"],
        "excluded": {},
        "context": {"work_binding": binding},
    }
    terminator = {"members": 1, "streams": len(stream_entries)}
    edit_manifest(manifest)
    edit_terminator(terminator)
    with path.open("wb") as handle:
        generation = STREAM_GENERATION.encode()
        handle.write(magic + struct.pack(">H", len(generation)) + generation + struct.pack(">I", 0))
        writer = ck._Writer(handle, cipher, STREAM_GENERATION)
        for header, body in [(member, STREAM_ORDINARY), *streams]:
            writer.frame("h", json.dumps(header).encode())
            if body:
                writer.frame("d", body)
            writer.frame("e", json.dumps(stream_ending(body)).encode())
        writer.frame("m", json.dumps(manifest).encode())
        writer.frame("z", json.dumps(terminator).encode())
    return path, manifest


def stream_rewrite_frames(path: Path, mutate) -> None:
    with path.open("rb") as handle:
        generation, escrow = ck._read_head(handle)
        frames = list(ck._frames(handle, STREAM_CIPHER, generation))
    mutate(frames)
    with path.open("wb") as handle:
        encoded = generation.encode()
        handle.write(b"ACK3" + struct.pack(">H", len(encoded)) + encoded + struct.pack(">I", len(escrow)) + escrow)
        writer = ck._Writer(handle, STREAM_CIPHER, generation)
        for kind, data in frames:
            writer.frame(kind, data)


def stream_omission_context(manifest: dict) -> None:
    from catchup_core.work_privacy import STREAM_POLICY

    manifest["context"]["work_binding"].update(schema="artha.work-checkpoint-binding.v3", stream_policy=STREAM_POLICY)
    manifest["context"]["work_stream_omissions"] = {
        "policy": STREAM_POLICY,
        "semantics": "observed_names_not_preserved",
        "roots": sorted(manifest["roots"]),
        "streams": [{**stream_identity(STREAM_OWNER, "file", "sec.endpointdlp"), "size": 17}],
    }


def stream_public_tree(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    document = root / "note.txt"
    document.write_bytes(STREAM_ORDINARY)
    folder = root / "folder"
    folder.mkdir()
    Path(str(document) + ":synthetic secret name").write_bytes(b"synthetic secret stream bytes")
    Path(str(document) + ":empty").write_bytes(b"")
    Path(str(folder) + ":directory stream").write_bytes(b"folder bytes")
    Path(str(root) + ":root stream").write_bytes(b"root bytes")
    return root, document


RENEWAL_NOW = datetime(2026, 1, 2, tzinfo=UTC)
RENEWAL_COHORT = {name: "a" * 64 for name in ("implementation", "configuration", "prompts", "packs", "model", "contract")}
RENEWAL_EVALUATOR = {"scripts/policy.py": b"synthetic evaluator", "scripts/presentation.py": b"presentation a"}
RENEWAL_BINDING = {"owner": "owner", "host": "synthetic", "repository": "example/project", "profile": "personal"}


def renewal_private_directory(path: Path) -> None:
    if os.name == "nt":
        from catchup_core.quarantine_fs_windows import ensure_private_directory

        ensure_private_directory(path)
    else:
        path.mkdir(mode=0o700, exist_ok=True)


def renewal_manifest(value: str = "a") -> dict:
    from dataclasses import asdict

    return {
        "schema": "artha.renewal-source.v1",
        "files": [
            asdict(SourceFile("scripts/presentation.py", hashlib.sha256(("presentation " + value).encode()).hexdigest(), "100644")),
            asdict(SourceFile("scripts/policy.py", hashlib.sha256(b"synthetic evaluator").hexdigest(), "100644")),
            asdict(SourceFile(".github/workflows/ci.yml", "a" * 64, "100644")),
        ],
    }


def renewal_target() -> dict:
    return {
        "source": renewal_manifest("b"),
        "cohort": {**RENEWAL_COHORT, "implementation": "b" * 64},
        "tree": "c" * 40,
        "context": "d" * 64,
        "authority": "0" * 64,
    }


def renewal_approve(authority, value=None):
    value = value or renewal_target()
    return authority.approve(value, expected_digest=digest(value), reviewed=True)


class SyntheticRemote:
    def __init__(self) -> None:
        self.commit = ""
        self.tip = "0" * 40
        self.effects: list[str] = []
        self.ambiguous = False

    def remote(self, repository: str) -> None:
        assert repository == "example/project"

    def tree(self) -> str:
        return "c" * 40

    def git(self, *args: str) -> None:
        self.effects.append(args[0])
        if args[0] == "commit":
            self.commit = "e" * 40
        elif args[0] == "push":
            self.tip = self.commit
            if self.ambiguous:
                raise RenewalError("Git delivery operation unavailable")


def renewal_git(root: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=" + str(root / "synthetic-empty-hooks"),
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "-C",
            str(root),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def lifecycle_req(cid: str = "c1") -> CatchupRequest:
    return CatchupRequest(cid, actor="ved", channel="full", mode=ExecutionMode.AUTHORITATIVE, host="localhost")


def lifecycle_fact_rows(ledger):
    return [tuple(r) for r in ledger.conn.execute("SELECT fact_id,status FROM facts ORDER BY fact_id")]


def build_lifecycle_env(tmp_path: Path):
    """Generator matching ``test_provider_observation_lifecycle.env``."""
    now = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
    ledger = lg.Ledger(tmp_path / "ledger.db", id_key=CORE_KEY, cipher=COORDINATOR_CIPHER, owner_id="ved").open()
    ledger.migrate()
    blobs = EncryptedBlobStore(tmp_path / "blobs", COORDINATOR_CIPHER, CORE_KEY)
    published: list[tuple[str, int]] = []

    def make(fetchers, **kw):
        all_fetchers = {
            "school_email": lambda: co.ConnectorFetch("school_email", "acct-a"),
            "calendar": lambda: co.ConnectorFetch("calendar", "acct-a"),
        }
        all_fetchers.update(fetchers)
        return co.Deps(
            ledger=ledger,
            pack=CORE_PACK,
            blobs=blobs,
            id_key=CORE_KEY,
            fetchers=all_fetchers,
            extractor=kw.pop("extractor", CORE_EXTRACT),
            subjects=["kid1", "kid2"],
            viewer="ved",
            now=lambda: now,
            source_class={"school_email": "school_email", "calendar": "calendar"},
            publisher=lambda cid, text, v: published.append((cid, v)),
            coverage_line=lambda: "Coverage: test.",
            **kw,
        )

    yield ledger, blobs, make, published
    ledger.close()


def build_renewal_authority_world(tmp_path: Path):
    """Build synthetic ``(authority, delegation, credentials)`` for renewal tests."""
    from catchup_core.renewal_authority import Authority
    from lib.credentials import CredentialStore

    local = tmp_path / "local"
    credentials = CredentialStore(backend=Keyring(), local=local)
    authority = Authority(local, RENEWAL_BINDING, credentials=credentials, now=lambda: RENEWAL_NOW, checkout=tmp_path / "repo")
    delegation = {
        "schema": "artha.renewal-delegation.v1",
        "binding": RENEWAL_BINDING,
        "base_source": renewal_manifest(),
        "base_cohort": RENEWAL_COHORT,
        "base_release": {"profile": "slice", "domains": ["kids_school"], "owner_approved_at": "original"},
        "protected": ["scripts/policy.py", ".github/workflows/ci.yml"],
        "dependencies": {"quality": ["scripts/policy.py"], "recovery": ["scripts/policy.py"]},
        "context": "d" * 64,
        "expires_at": None,
        "gates": [{"workflow": ".github/workflows/ci.yml", "definition_sha256": "a" * 64, "jobs": ["test"]}],
        "domains": ["kids_school"],
        "routine": ["scripts/presentation.py"],
        "evaluator": {path: hashlib.sha256(raw).hexdigest() for path, raw in RENEWAL_EVALUATOR.items()},
    }
    authority.enroll(
        delegation,
        expected_digest=digest(delegation),
        reviewed=True,
        validate_base=lambda _: None,
        evaluator=RENEWAL_EVALUATOR,
    )
    return authority, delegation, credentials


def build_integrated_app(tmp_path: Path):
    """Generator matching ``test_integrated_surfaces.app``."""
    from catchup_core.ledger import FactInput, SignalInput

    led = Ledger(tmp_path / "local/catchup_core/ledger.db", id_key=CORE_KEY, cipher=REGRESSION_CIPHER).open()
    led.migrate()
    blobs = EncryptedBlobStore(led._path.parent / "blobs", REGRESSION_CIPHER, CORE_KEY)
    ref = blobs.put("mail", "Registration closes 2026-10-14")
    sid, _ = led.upsert_signal(SignalInput("school_email", "family", "m1", "v1", CORE_NOW.isoformat(), "h1", ref))
    fid = led.put_fact(
        FactInput(
            domain="kids_school",
            predicate="exam_registration_deadline",
            value="2026-10-14",
            primary_subject_id="kid1",
            observed_at=CORE_NOW.isoformat(),
            evidence=((sid, 0, 30),),
        )
    )
    led.upsert_alert("exam", domain="kids_school", primary_subject_id="kid1", severity="P1", fact_ids=[fid])
    deps = co.Deps(led, CORE_PACK, blobs, CORE_KEY, {}, lambda _: '{"complete":true,"candidates":[]}', ["kid1"], "owner", lambda: CORE_NOW)
    (tmp_path / "state").mkdir()
    yield tmp_path, deps, fid, sid
    led.close()


def goal_metric(kind: str = "outcome", **changes):
    definition = {
        "kind": kind,
        "domain": "goals",
        "predicate": "goal_quantity" if kind == "outcome" else "goal_checkin",
        "subject": "owner",
        "sources": ["capture:owner"],
        "period_days": 7,
        "freshness_hours": 48,
        "target": "150.00" if kind == "outcome" else 3,
    }
    if kind == "outcome":
        definition.update(unit="USD", baseline="100.00", comparison="at_least")
    definition.update(changes)
    return {"id": "G1", "owner": "owner", "title": "Practice or save", "status": "active", "metric": definition}


def goal_evidence(ledger, text, *, predicate="goal_quantity", scope="owner", subject="owner", provenance=None, observed_at=None, status=None):
    from catchup_core.contracts import FactStatus, ProcessingState, Provenance
    from catchup_core.ledger import FactInput, SignalInput

    status = FactStatus.CURRENT if status is None else status
    provenance = Provenance.VERIFIED_SOURCE if provenance is None else provenance
    sid, _ = ledger.upsert_signal(
        SignalInput(
            "capture",
            scope,
            "entry-" + str(ledger.conn.total_changes),
            "v1",
            CORE_NOW.isoformat(),
            "h-" + str(ledger.conn.total_changes),
            "synthetic",
        )
    )
    fid = ledger.put_fact(
        FactInput(
            "goals",
            predicate,
            text,
            subject,
            observed_at or CORE_NOW.isoformat(),
            evidence=((sid, 0, len(text.encode())),),
            status=status,
            provenance=provenance,
        )
    )
    ledger.set_processing_state(sid, ProcessingState.RECONCILED)
    return fid


def goal_coverage(ledger, **changes):
    ledger.set_coverage(
        "capture",
        "owner",
        changes.get("status", "complete"),
        catchup_id="c",
        completed_at=changes.get("completed_at", CORE_NOW.isoformat()),
    )


def goal_payload(ledger):
    row = ledger.conn.execute("SELECT payload_encrypted FROM goal_observations WHERE goal_id='G1'").fetchone()
    assert row
    return ledger._cipher.decrypt_json(row[0], aad=b"goal:G1")


def work_stream_quarantine_authority():
    return {
        "actor": "S-1-5-21-1001",
        "owner": "S-1-5-21-1001",
        "profile": "work",
        "work": {"identity": "a" * 64, "generation": 1, "head_sha256": "b" * 64},
    }


def set_test_dacl(path: Path, sddl: str) -> None:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_handle = kernel32.CreateFileW
    open_handle.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    open_handle.restype = wintypes.HANDLE
    handle = open_handle(str(path), 0x00040000 | 0x00020000, 0x1 | 0x2, None, 3, 0x02000000 | 0x00200000, None)
    if handle in {None, wintypes.HANDLE(-1).value}:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    convert.restype = wintypes.BOOL
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        get_dacl = advapi32.GetSecurityDescriptorDacl
        get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
        get_dacl.restype = wintypes.BOOL
        if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        set_security = advapi32.SetSecurityInfo
        set_security.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        set_security.restype = wintypes.DWORD
        result = set_security(handle, 1, 0x00000004 | 0x80000000, None, None, dacl, None)
        if result:
            raise OSError(result, "SetSecurityInfo failed")
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.LocalFree(descriptor)
        kernel32.CloseHandle(handle)


WORK_PRIVACY_KEY = b"x" * 32
WORK_IDENTITY = {"platform": "win32", "machine": "synthetic-machine", "user": "S-1-5-21-1001"}
WorkBackend = InMemorySealedKeyring


class FileBackend:
    """Synthetic file-backed credential backend shared across parent/child processes."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(exist_ok=True)

    def _path(self, service: str, account: str) -> Path:
        return self.directory / hashlib.sha256((service + "\0" + account).encode()).hexdigest()

    def get_password(self, service: str, account: str) -> str | None:
        path = self._path(service, account)
        return path.read_text() if path.exists() else None

    def set_password(self, service: str, account: str, value: str) -> None:
        self._path(service, account).write_text(value)


class RenewalWorkspace:
    """Synthetic git workspace stand-in for renewal delivery/entry/process tests."""

    def __init__(self) -> None:
        self.commit = ""
        self.tip = "0" * 40
        self.effects: list[str] = []
        self.ambiguous = False

    def remote(self, repository: str) -> None:
        assert repository == "example/project"

    def tree(self) -> str:
        return "c" * 40

    def git(self, *args: str) -> str:
        self.effects.append(args[0])
        if args[0] == "commit":
            self.commit = "e" * 40
        elif args[0] == "push":
            self.tip = self.commit
            if self.ambiguous:
                raise RenewalError("Git delivery operation unavailable")
        return ""

    def clean_commit(self) -> str:
        if not self.commit:
            raise RenewalError("not committed")
        return self.commit

    def remote_tip(self) -> str:
        return self.tip


