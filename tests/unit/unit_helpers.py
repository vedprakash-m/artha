"""Shared synthetic helpers for ``tests/unit/`` (eliminates cross-test module imports)."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from actions.base import ActionProposal


class MemoryKeyring:
    """In-memory synthetic keyring backend for unit tests."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.events: list[tuple[str, str, str]] = []

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.events.append(("set", service, account))
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        self.events.append(("delete", service, account))
        self.values.pop((service, account), None)


class SQLiteKeyring:
    """Synthetic, process-shared stand-in for durable OS credential operations."""

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


def install_empty_action_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``action_credentials.prepare`` with an empty, offline capability token."""
    from action_queue import compute_payload_hash
    from lib import action_credentials

    def empty(module, item, timeout, *, operation="execute", since=""):
        now = time.time()
        return {
            "schema": "artha.action-credentials.v1",
            "module": module,
            "payload_hash": compute_payload_hash(item),
            "operation": operation,
            "since": since,
            "nonce": "b" * 32,
            "issued_at": now,
            "expires_at": now + timeout + 2,
            "records": {},
            "api_keys": {},
        }

    monkeypatch.setattr(action_credentials, "prepare", empty)


def make_action_proposal() -> ActionProposal:
    """Build a synthetic ``reminder_create`` ``ActionProposal``."""
    return ActionProposal(
        "synthetic",
        "reminder_create",
        "home",
        "Inspect filter",
        "",
        {"title": "Inspect filter"},
        "high",
        1,
        "standard",
        False,
        None,
        None,
    )


def make_modify_proposal(**param_overrides) -> ActionProposal:
    """Build a synthetic ``calendar_modify`` ``ActionProposal``."""
    params = {
        "event_id": "event123",
        "calendar_id": "primary",
        "updates": {"summary": "Updated Meeting"},
    }
    params.update(param_overrides)
    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type="calendar_modify",
        domain="work",
        title="Modify calendar event",
        description="",
        parameters=params,
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=True,
        undo_window_sec=300,
        expires_at=None,
        source_step=None,
        source_skill=None,
        linked_oi=None,
    )


WORK_IDENTITY = {"platform": "win32", "machine": "synthetic-machine", "user": "synthetic-user"}


class WorkBackend:
    """In-memory synthetic keyring backend for Work publication tests."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value


def enroll_work(work: dict) -> object:
    """Enroll synthetic Work publication protocol."""
    return work["make"]().enroll(b"k" * 32, reviewed=True, installation_id="synthetic-installation")


def never_legacy() -> None:
    """Assert legacy writer callbacks never execute after Work enrollment."""
    pytest.fail("legacy writer must not run after enrollment")


def create_evidence_case(tmp_path: Path):
    """Create a synthetic sealed ledger, action proposal, and worker evidence context."""
    from action_queue import compute_payload_hash
    from dataclasses import asdict
    from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
    from catchup_core.ids import HmacKey
    from catchup_core.ledger import FactInput, Ledger, SignalInput
    from lib import action_evidence as evidence

    root = tmp_path / "installation"
    (root / "config").mkdir(parents=True)
    pack = Path(__file__).resolve().parents[2] / "scripts/catchup_core/packs/kids_school.yaml"
    dest = root / "scripts/catchup_core/packs/kids_school.yaml"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(pack.read_bytes())
    (root / "config/catchup_core.yaml").write_text("synthetic: true\n")
    path = tmp_path / "private/catchup_core/ledger.db"
    now = datetime.now(UTC)
    clock = lambda: now.isoformat()
    cipher = EnvelopeCipher(StaticKeyProvider({1: b"e" * 32}))
    ledger = Ledger(
        path,
        id_key=HmacKey(1, b"i" * 32),
        cipher=cipher,
        owner_id="owner",
        consent_subjects=frozenset({"adult"}),
        known_subjects=frozenset({"owner", "adult"}),
        clock=clock,
    )
    ledger.open()
    ledger.migrate()
    ledger.grant_consent("adult", "adult", "*", ["view", "export"], "owner")
    sid, _ = ledger.upsert_signal(
        SignalInput("school_email", "personal", "mail-1", "v1", clock(), "synthetic-hmac", "synthetic-blob")
    )
    fact = FactInput("kids_school", "deadline", "SYNTHETIC_PRIVATE_VALUE", "adult", clock(), evidence=((sid, 0, 5),))
    fid = ledger.put_fact(fact)
    ledger.conn.execute(
        "INSERT INTO connector_coverage(connector,account_scope,completed_at,status) VALUES('school_email','personal',?,'complete')",
        (clock(),),
    )
    checkpoint = ledger.seal_privacy()
    pre = {
        "core_derived": True,
        "fact_versions": {fid: ledger.get_fact(fid, "owner").recorded_at},
        "subjects": ["adult"],
        "permission_generation": ledger._permission_generation(),
        "fresh_until": (now + timedelta(hours=1)).isoformat(),
    }
    proposal = ActionProposal(
        "synthetic",
        "instruction_sheet",
        "kids_school",
        "Synthetic instruction",
        "",
        {"preconditions": pre},
        "high",
        1,
        "standard",
        False,
        None,
        None,
        source_skill="catchup_core",
    )
    config = SimpleNamespace(
        domains=["kids_school"],
        viewer="owner",
        subjects=["owner", "adult"],
        consent_required=frozenset({"adult"}),
        sources={"school_email": {"account_scope": "personal"}},
    )

    class Backend:
        def get_password(self, service: str, account: str) -> str:
            assert (service, account) == ("artha-privacy-authoritative", "checkpoint")
            return json.dumps(asdict(checkpoint))

    context = evidence._metadata_context(root, ledger, config, Backend(), compute_payload_hash(proposal), pre)
    queue = tmp_path / "actions.db"
    sqlite3.connect(queue).close()
    evidence.bind(queue, proposal, context)
    return SimpleNamespace(
        root=root,
        ledger=ledger,
        fact=fact,
        fid=fid,
        sid=sid,
        pre=pre,
        proposal=proposal,
        context=context,
        queue=queue,
        config=config,
        now=now,
    )
