"""Owner migration preview/apply and crash-resumable receipt."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from catchup_core import crypto
from catchup_core import ledger as lg
from catchup_core import migration_cli
from core_helpers import CORE_KEY as KEY

CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x19" * 32}))


def _backup(path: Path) -> Path:
    target = path.with_suffix(".migration-test.db")
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    assert target.exists()
    return target


@pytest.fixture
def old_ledger(tmp_path, monkeypatch):
    available = lg.Ledger.available_migrations()
    monkeypatch.setattr(lg.Ledger, "available_migrations", staticmethod(lambda: available[:-1]))
    path = tmp_path / "old.db"
    with lg.Ledger(path, id_key=KEY, cipher=CIPHER) as led:
        led.migrate()
        assert led.schema_version() == available[-2][0]
    monkeypatch.setattr(lg.Ledger, "available_migrations", staticmethod(lambda: available))
    return path


def test_migration_preview_binds_exact_sql_and_counts(old_ledger):
    with lg.Ledger(old_ledger, id_key=KEY, cipher=CIPHER).open(read_only=True) as led:
        first = migration_cli.preview(led)
        second = migration_cli.preview(led)
    assert {k: v for k, v in first.items() if k != "free_bytes"} == {
        k: v for k, v in second.items() if k != "free_bytes"}
    assert first["current_schema"] < first["target_schema"]
    assert first["digest"] == migration_cli.plan_digest(led.available_migrations())


def test_apply_requires_exact_preview_and_verified_backup(old_ledger):
    with lg.Ledger(old_ledger, id_key=KEY, cipher=CIPHER) as led:
        plan = migration_cli.preview(led)
        receipt = migration_cli.apply(led, plan["digest"], backup=_backup)
        assert receipt["schema"] == led.schema_version() == plan["target_schema"]
        assert receipt["integrity"] == "ok"
        assert receipt["duplicate_binding_groups"] == 0


def test_apply_is_idempotent_after_commit(old_ledger):
    with lg.Ledger(old_ledger, id_key=KEY, cipher=CIPHER) as led:
        digest = migration_cli.preview(led)["digest"]
        calls = []
        migration_cli.apply(led, digest, backup=lambda path: calls.append(path) or _backup(path))
        second = migration_cli.apply(led, digest, backup=lambda path: pytest.fail("backup on no-op"))
    assert len(calls) == 1
    assert second["already_applied"] is True


def test_resume_finishes_post_commit_verification_without_remigrating(old_ledger):
    with lg.Ledger(old_ledger, id_key=KEY, cipher=CIPHER) as led:
        digest = migration_cli.preview(led)["digest"]
        migration_cli.apply(led, digest, backup=_backup)
        led.conn.execute(
            "UPDATE scope_admission_state SET verified_migration=NULL,receipt_digest=NULL")
        seals = []
        original = led.seal_privacy
        led.seal_privacy = lambda: seals.append(True) or original()
        resumed = migration_cli.apply(
            led, digest, backup=lambda _path: pytest.fail("committed schema must not remigrate"))
    assert resumed["already_applied"] is True and seals == [True]


def test_migration_cli_parser_and_mutation_gate():
    from catchup_core import cli
    args = cli.parse_args(["migrate"])
    assert args.command == "migrate" and not cli._mutates(args)
    apply = cli.parse_args(["migrate", "--apply", "a" * 64])
    assert apply.apply == "a" * 64 and cli._mutates(apply)
