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


def test_apply_rejects_invalid_or_changed_preview_digest(old_ledger):
    with lg.Ledger(old_ledger, id_key=KEY, cipher=CIPHER) as led:
        digest = migration_cli.preview(led)["digest"]
        with pytest.raises(lg.LedgerError, match="exact preview digest"):
            migration_cli.apply(
                led, "not-a-digest", backup=lambda _path: pytest.fail("invalid plans do not back up"))
        with pytest.raises(lg.LedgerError, match="sources changed"):
            migration_cli.apply(
                led, "0" * 64 if digest != "0" * 64 else "1" * 64,
                backup=lambda _path: pytest.fail("changed plans do not back up"))


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


def test_resume_refuses_a_missing_migration_receipt_state(old_ledger):
    with lg.Ledger(old_ledger, id_key=KEY, cipher=CIPHER) as led:
        digest = migration_cli.preview(led)["digest"]
        migration_cli.apply(led, digest, backup=_backup)
        led.conn.execute("DELETE FROM scope_admission_state WHERE singleton=1")
        with pytest.raises(lg.LedgerError, match="receipt state is unavailable"):
            migration_cli.apply(
                led, digest, backup=lambda _path: pytest.fail("resume must not remigrate"))


def test_migration_verifies_required_resumable_tables(old_ledger, monkeypatch):
    with lg.Ledger(old_ledger, id_key=KEY, cipher=CIPHER) as led:
        digest = migration_cli.preview(led)["digest"]
        migrate = led.migrate

        def migrate_without_coverage_hold_table(*args, **kwargs):
            result = migrate(*args, **kwargs)
            led.conn.execute("DROP TABLE coverage_holds")
            return result

        monkeypatch.setattr(led, "migrate", migrate_without_coverage_hold_table)
        with pytest.raises(lg.LedgerError, match="did not create coverage_holds"):
            migration_cli.apply(led, digest, backup=_backup)


def test_migration_refuses_copied_source_enrollment_state(old_ledger, monkeypatch):
    with lg.Ledger(old_ledger, id_key=KEY, cipher=CIPHER) as led:
        digest = migration_cli.preview(led)["digest"]
        migrate = led.migrate

        def migrate_with_stale_enrollment(*args, **kwargs):
            result = migrate(*args, **kwargs)
            led.conn.execute(
                "INSERT INTO source_enrollments("
                "source_name,connector,provider_account_id,account_scope,query_digest,"
                "policy_digest,subjects_json,action,permission_generation,scope_epoch,"
                "proof,reviewer,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("home_email", "gmail", "account", "personal", "query", "policy", "[\"ved\"]",
                 "enroll", 1, 1, "proof", "ved", "2026-11-18T00:00:00Z"),
            )
            return result

        monkeypatch.setattr(led, "migrate", migrate_with_stale_enrollment)
        with pytest.raises(lg.LedgerError, match="copied source enrollment"):
            migration_cli.apply(led, digest, backup=_backup)


def test_sqlite_backup_verification_refuses_missing_or_empty_file(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(lg.LedgerError, match="missing or empty"):
        migration_cli.verify_sqlite_backup(missing)
    missing.write_bytes(b"")
    with pytest.raises(lg.LedgerError, match="missing or empty"):
        migration_cli.verify_sqlite_backup(missing)


def test_migration_cli_parser_and_mutation_gate():
    from catchup_core import cli
    args = cli.parse_args(["migrate"])
    assert args.command == "migrate" and not cli._mutates(args)
    apply = cli.parse_args(["migrate", "--apply", "a" * 64])
    assert apply.apply == "a" * 64 and cli._mutates(apply)
