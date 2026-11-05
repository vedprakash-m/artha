"""
test_backup.py — Tests for scripts/backup.py (GFS archive engine)
==================================================================
77+ tests covering:
  - GFS tier logic
  - Manifest round-trip
  - backup_snapshot (public API with registry parameter)
  - Pruning
  - Validation
  - Backup status
  - Backup registry loading (load_backup_registry)
  - Comprehensive multi-source-type snapshots
  - Restore (catalog-based)
  - Install (explicit ZIP)
  - --data-only mode
  - Structural guards (no circular import, _config propagation, function absence)
  - CLI subcommands
  - Key management (export-key, import-key)

Architecture note:
  All tests patch foundation._config via monkeypatch.setitem for consistent
  path redirection. backup.py imports _config from foundation (same object),
  so one fixture patch propagates to all modules. See bkp-rst.md §3.3.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import backup as backup
import foundation as foundation
import vault as vault


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_age_bytes(label: str = "test") -> bytes:
    """Return bytes that pass foundation.is_valid_age_file (header + padding)."""
    return ("age-encryption.org/v1\n-> X25519 fake\n" + "A" * 80 + f"\n--- {label}").encode()


def _good_markdown() -> bytes:
    return b"---\nschema_version: '1.0'\n---\n# Synthetic state\n" + b"word " * 50


def _create_test_zip(
    backup_dir: Path,
    tier: str,
    date_str: str,
    files_dict: dict,
) -> Path:
    """Create a valid backup ZIP for tests.

    files_dict: {arc_path: (content_bytes, source_type, restore_path, name)}
    Returns the Path of the created ZIP file.
    """
    tier_dir = backup_dir / tier
    tier_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tier_dir / f"{date_str}.zip"
    internal_files = {}
    with zipfile.ZipFile(str(zip_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arc_path, (content, source_type, restore_path, name) in files_dict.items():
            sha256 = hashlib.sha256(content).hexdigest()
            zf.writestr(arc_path, content)
            internal_files[arc_path] = {
                "name": name, "sha256": sha256, "size": len(content),
                "source_type": source_type, "restore_path": restore_path,
            }
        zf.writestr("manifest.json", json.dumps({
            "artha_backup_version": "2",
            "created": f"{date_str}T00:00:00+00:00",
            "date": date_str, "tier": tier,
            "files": internal_files,
        }))
    return zip_path


def _create_v3_zip(backup_dir: Path, date_str: str, records: dict) -> Path:
    """Create a synthetic version-3 ZIP; records map logical paths to payload tuples."""
    tier = "daily"
    tier_dir = backup_dir / tier
    tier_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tier_dir / f"{date_str}.zip"
    files = {}
    payloads = {}
    for index, (restore_path, record) in enumerate(records.items(), start=1):
        source_type, name, data, streams = record
        data_entry = f"payload/{index:06d}.age"
        payloads[data_entry] = data
        files[restore_path] = {
            "restore_path": restore_path,
            "source_type": source_type,
            "name": name,
            "data": {
                "entry": data_entry,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            },
            "streams": {},
        }
        for stream_index, (stream_name, stream_data) in enumerate(streams.items(), start=1):
            entry = f"payload/{index:06d}-s{stream_index:04d}.age"
            payloads[entry] = stream_data
            files[restore_path]["streams"][stream_name] = {
                "entry": entry,
                "sha256": hashlib.sha256(stream_data).hexdigest(),
                "size": len(stream_data),
            }
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry, payload in payloads.items():
            archive.writestr(entry, payload)
        archive.writestr("manifest.json", json.dumps({
            "artha_backup_version": "3",
            "created": f"{date_str}T00:00:00+00:00",
            "date": date_str,
            "tier": tier,
            "files": files,
        }))
    manifest = backup._load_manifest()
    manifest["snapshots"][f"{tier}/{zip_path.name}"] = {
        "date": date_str,
        "tier": tier,
        "sha256": backup._file_sha256(zip_path),
        "size": zip_path.stat().st_size,
        "file_count": len(files),
        "stream_count": sum(len(record["streams"]) for record in files.values()),
    }
    backup._save_manifest(manifest)
    return zip_path


# ---------------------------------------------------------------------------
# Registry helper (minimal test registry)
# ---------------------------------------------------------------------------

def _make_test_registry(state_dir: Path, config_dir: Path = None) -> list:
    """Build a minimal backup registry for tests."""
    entries = [
        {"name": "finance", "source_type": "state_encrypted",
         "source_path": state_dir / "finance.md.age",
         "restore_path": "state/finance.md.age"},
        {"name": "goals", "source_type": "state_plain",
         "source_path": state_dir / "goals.md",
         "restore_path": "state/goals.md"},
    ]
    if config_dir is not None:
        entries.append({
            "name": "cfg__config__user_profile_yaml",
            "source_type": "config",
            "source_path": config_dir / "user_profile.yaml",
            "restore_path": "config/user_profile.yaml",
        })
    return entries


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_backup_env(temp_artha_dir, monkeypatch):
    """Redirect all Artha paths to a temp directory via foundation._config.

    Patches foundation._config (the single source of truth) so backup.py,
    vault.py, and any other module reading from _config see the temp paths.
    Also pre-seeds a manifest with last_validate=today to suppress
    auto-validation in backup_snapshot() tests (see bkp-rst.md R11 / RC-19).
    """
    state_dir  = temp_artha_dir / "state"
    config_dir = temp_artha_dir / "config"
    backup_dir = temp_artha_dir / "backups"

    # Single fixture patch — propagates to all modules via shared _config dict
    monkeypatch.setitem(foundation._config, "ARTHA_DIR",        temp_artha_dir)
    monkeypatch.setitem(foundation._config, "STATE_DIR",        state_dir)
    monkeypatch.setitem(foundation._config, "CONFIG_DIR",       config_dir)
    monkeypatch.setitem(foundation._config, "AUDIT_LOG",        state_dir / "audit.md")
    monkeypatch.setitem(foundation._config, "LOCK_FILE",        temp_artha_dir / ".artha-decrypted")
    monkeypatch.setitem(foundation._config, "BACKUP_DIR",       backup_dir)
    monkeypatch.setitem(foundation._config, "BACKUP_MANIFEST",  backup_dir / "manifest.json")
    monkeypatch.setitem(foundation._config, "ARTHA_LOCAL_DIR",  temp_artha_dir / ".artha-local")
    from lib.knowledge_graph import KnowledgeGraph
    monkeypatch.setattr(KnowledgeGraph, "__init__", lambda self: None)
    monkeypatch.setattr(KnowledgeGraph, "backup", lambda self, tier: None)

    # Seed config/settings.md for get_public_key()
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.md").write_text('age_recipient: "age1mockpublickey"\n')

    # Pre-seed manifest with last_validate=today to suppress auto-validation
    backup_dir.mkdir(parents=True, exist_ok=True)
    today_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"last_validate": today_iso, "snapshots": {}}, indent=2) + "\n",
        encoding="utf-8",
    )

    return temp_artha_dir


# ---------------------------------------------------------------------------
# §1 GFS Tier Logic
# ---------------------------------------------------------------------------

class TestGFSTierLogic:
    """_get_backup_tier must promote dates to the correct tier."""

    def test_daily_monday(self):
        assert backup._get_backup_tier(date(2026, 3, 9)) == "daily"   # Monday

    def test_daily_saturday(self):
        assert backup._get_backup_tier(date(2026, 3, 14)) == "daily"  # Saturday

    def test_weekly_sunday_mid_month(self):
        assert backup._get_backup_tier(date(2026, 3, 8)) == "weekly"

    def test_monthly_beats_sunday(self):
        # Feb 28 2027 (non-leap Monday, last day of Feb): monthly wins over any day
        assert backup._get_backup_tier(date(2027, 2, 28)) == "monthly"

    def test_monthly_last_day_jan(self):
        assert backup._get_backup_tier(date(2026, 1, 31)) == "monthly"

    def test_monthly_last_day_april(self):
        assert backup._get_backup_tier(date(2026, 4, 30)) == "monthly"

    def test_monthly_feb28_non_leap(self):
        assert backup._get_backup_tier(date(2025, 2, 28)) == "monthly"

    def test_monthly_feb29_leap(self):
        assert backup._get_backup_tier(date(2028, 2, 29)) == "monthly"

    def test_yearly_dec31(self):
        assert backup._get_backup_tier(date(2025, 12, 31)) == "yearly"

    def test_yearly_beats_monthly_beats_sunday(self):
        assert backup._get_backup_tier(date(2023, 12, 31)) == "yearly"

    def test_not_daily_nov30(self):
        assert backup._get_backup_tier(date(2026, 11, 30)) == "monthly"


# ---------------------------------------------------------------------------
# §2 Manifest
# ---------------------------------------------------------------------------

class TestManifest:
    """_load_manifest / _save_manifest round-trip and error handling."""

    def test_load_returns_empty_when_missing(self, mock_backup_env):
        # Remove the pre-seeded manifest to test the empty case
        foundation._config["BACKUP_MANIFEST"].unlink(missing_ok=True)
        m = backup._load_manifest()
        assert m == {"last_validate": None, "snapshots": {}}

    def test_save_and_load_round_trip(self, mock_backup_env):
        manifest = {"snapshots": {"daily/2026-03-13.zip": {"sha256": "abc"}}, "last_validate": None}
        backup._save_manifest(manifest)
        loaded = backup._load_manifest()
        assert loaded["snapshots"]["daily/2026-03-13.zip"]["sha256"] == "abc"

    @pytest.mark.parametrize("content", [
        "{ not valid json }", "[]", '{"snapshots": []}',
        '{"snapshots": {"daily/example.zip": null}}', b"\xff",
    ])
    def test_invalid_catalog_is_not_treated_as_empty(self, mock_backup_env, content):
        manifest_path = foundation._config["BACKUP_MANIFEST"]
        original = content if isinstance(content, bytes) else content.encode("utf-8")
        manifest_path.write_bytes(original)
        with pytest.raises(RuntimeError, match="catalog"):
            backup._load_manifest()
        assert manifest_path.read_bytes() == original

    def test_unreadable_catalog_is_not_treated_as_missing(self, mock_backup_env):
        manifest_path = foundation._config["BACKUP_MANIFEST"]
        original = manifest_path.read_bytes()
        with patch.object(Path, "read_text", side_effect=PermissionError("synthetic")), \
             pytest.raises(RuntimeError, match="catalog"):
            backup._load_manifest()
        assert manifest_path.read_bytes() == original

    def test_failed_catalog_publication_is_not_success(self, mock_backup_env):
        manifest_path = foundation._config["BACKUP_MANIFEST"]
        original = manifest_path.read_bytes()
        with patch("backup.os.replace", side_effect=PermissionError("synthetic")), \
             pytest.raises(RuntimeError, match="catalog"):
            backup._save_manifest({"snapshots": {}, "last_validate": None})
        assert manifest_path.read_bytes() == original
        assert not Path(str(manifest_path) + ".tmp").exists()

    def test_save_is_atomic(self, mock_backup_env):
        backup._save_manifest({"snapshots": {}, "last_validate": None})
        tmp = Path(str(foundation._config["BACKUP_MANIFEST"]) + ".tmp")
        assert not tmp.exists()
        assert foundation._config["BACKUP_MANIFEST"].exists()


# ---------------------------------------------------------------------------
# §3 backup_snapshot
# ---------------------------------------------------------------------------

class TestBackupSnapshot:
    """backup_snapshot creates a single ZIP per GFS tier-day, updates outer manifest."""

    def _seed_age_files(self, state_dir, config_dir, domains=("immigration", "finance")):
        import yaml as _yaml
        state_files = [{"name": d, "sensitive": True} for d in domains]
        profile = {"backup": {"state_files": state_files, "config_files": []}}
        profile_path = config_dir / "user_profile.yaml"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(_yaml.dump(profile), encoding="utf-8")
        for d in domains:
            (state_dir / f"{d}.md.age").write_bytes(_fake_age_bytes(d))

    def _make_registry(self, state_dir, domains):
        return [
            {"name": d, "source_type": "state_encrypted",
             "source_path": state_dir / f"{d}.md.age",
             "restore_path": f"state/{d}.md.age"}
            for d in domains
        ]

    def test_snapshot_creates_zip_in_daily_tier(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        self._seed_age_files(state_dir, config_dir)
        registry = self._make_registry(state_dir, ["immigration", "finance"])
        count = backup.backup_snapshot(registry, today=date(2026, 3, 9))
        assert count == 2
        assert (foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip").exists()

    def test_snapshot_sunday_goes_to_weekly(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        self._seed_age_files(state_dir, config_dir)
        registry = self._make_registry(state_dir, ["immigration", "finance"])
        backup.backup_snapshot(registry, today=date(2026, 3, 8))
        assert (foundation._config["BACKUP_DIR"] / "weekly" / "2026-03-08.zip").exists()
        assert not (foundation._config["BACKUP_DIR"] / "daily" / "2026-03-08.zip").exists()

    def test_snapshot_month_end_goes_to_monthly(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        self._seed_age_files(state_dir, config_dir)
        registry = self._make_registry(state_dir, ["immigration", "finance"])
        backup.backup_snapshot(registry, today=date(2026, 2, 28))
        assert (foundation._config["BACKUP_DIR"] / "monthly" / "2026-02-28.zip").exists()

    def test_snapshot_dec31_goes_to_yearly(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        self._seed_age_files(state_dir, config_dir)
        registry = self._make_registry(state_dir, ["immigration", "finance"])
        backup.backup_snapshot(registry, today=date(2025, 12, 31))
        assert (foundation._config["BACKUP_DIR"] / "yearly" / "2025-12-31.zip").exists()

    def test_snapshot_writes_outer_manifest_with_checksum(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        self._seed_age_files(state_dir, config_dir)
        registry = self._make_registry(state_dir, ["immigration", "finance"])
        backup.backup_snapshot(registry, today=date(2026, 3, 9))
        m = backup._load_manifest()
        key = "daily/2026-03-09.zip"
        assert key in m["snapshots"]
        meta = m["snapshots"][key]
        assert len(meta["sha256"]) == 64
        assert meta["tier"] == "daily"
        assert meta["date"] == "2026-03-09"
        assert meta["file_count"] == 2

    def test_snapshot_zip_contains_internal_manifest(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        self._seed_age_files(state_dir, config_dir)
        registry = self._make_registry(state_dir, ["immigration", "finance"])
        backup.backup_snapshot(registry, today=date(2026, 3, 9))
        zip_path = foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip"
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            assert "manifest.json" in zf.namelist()

    def test_snapshot_is_atomic_no_tmp_remains(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        self._seed_age_files(state_dir, config_dir)
        registry = self._make_registry(state_dir, ["immigration", "finance"])
        backup.backup_snapshot(registry, today=date(2026, 3, 9))
        assert list(foundation._config["BACKUP_DIR"].rglob("*.tmp")) == []

    def test_snapshot_preserves_valid_encrypted_file(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        (state_dir / "immigration.md.age").write_bytes(_fake_age_bytes("immigration"))
        registry = [
            {"name": "immigration", "source_type": "state_encrypted",
             "source_path": state_dir / "immigration.md.age",
             "restore_path": "state/immigration.md.age"},
        ]
        count = backup.backup_snapshot(registry, today=date(2026, 3, 9))
        assert count == 1

    def test_snapshot_returns_zero_when_no_files(self, mock_backup_env):
        state_dir = foundation._config["STATE_DIR"]
        registry = [
            {"name": "finance", "source_type": "state_encrypted",
             "source_path": state_dir / "finance.md.age",
             "restore_path": "state/finance.md.age"},
        ]
        count = backup.backup_snapshot(registry, today=date(2026, 3, 9))
        assert count == 0

    def test_snapshot_rejects_partial_inventory_with_corrupt_age(self, mock_backup_env):
        """A corrupt .age file prevents publishing a partial registry snapshot."""
        state_dir = foundation._config["STATE_DIR"]
        # Write a 7-byte stub like the real-world corruption
        (state_dir / "immigration.md.age").write_text("enc-imm")
        # Write a valid file to ensure mixed-health registries still work
        (state_dir / "finance.md.age").write_bytes(_fake_age_bytes("finance"))
        registry = [
            {"name": "immigration", "source_type": "state_encrypted",
             "source_path": state_dir / "immigration.md.age",
             "restore_path": "state/immigration.md.age"},
            {"name": "finance", "source_type": "state_encrypted",
             "source_path": state_dir / "finance.md.age",
             "restore_path": "state/finance.md.age"},
        ]
        count = backup.backup_snapshot(registry, today=date(2026, 3, 9))
        assert count == 0
        assert not (foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip").exists()


# ---------------------------------------------------------------------------
# §4 Pruning
# ---------------------------------------------------------------------------

class TestPruning:
    """_prune_backups enforces ZIP retention limits per GFS tier."""

    def _create_daily_zips(self, backup_dir, n_days):
        tier_dir = backup_dir / "daily"
        tier_dir.mkdir(parents=True, exist_ok=True)
        manifest = backup._load_manifest()
        for i in range(n_days):
            d = date(2026, 3, i + 1)
            z = tier_dir / f"{d.isoformat()}.zip"
            with zipfile.ZipFile(str(z), "w") as zf:
                zf.writestr("manifest.json", json.dumps({"date": d.isoformat(), "tier": "daily", "files": {}}))
            manifest["snapshots"][f"daily/{z.name}"] = {
                "date": d.isoformat(), "tier": "daily",
                "file_count": 1, "sha256": "x" * 64, "size": 100,
            }
        backup._save_manifest(manifest)

    def test_prune_keeps_exactly_n(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._create_daily_zips(backup_dir, 10)
        backup._prune_backups("daily", 7)
        remaining = sorted((backup_dir / "daily").glob("*.zip"))
        assert len(remaining) == 7
        assert remaining[-1].stem == "2026-03-10"
        assert remaining[0].stem == "2026-03-04"

    def test_prune_does_nothing_when_under_limit(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._create_daily_zips(backup_dir, 5)
        backup._prune_backups("daily", 7)
        remaining = list((backup_dir / "daily").glob("*.zip"))
        assert len(remaining) == 5

    def test_prune_removes_manifest_entries(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._create_daily_zips(backup_dir, 10)
        backup._prune_backups("daily", 7)
        loaded = backup._load_manifest()
        assert len([k for k in loaded["snapshots"] if k.startswith("daily/")]) == 7

    def test_yearly_not_pruned_because_none_keep(self, mock_backup_env):
        assert backup.GFS_RETENTION["yearly"] is None


# ---------------------------------------------------------------------------
# §5 Validate Backup
# ---------------------------------------------------------------------------

class TestValidateBackup:
    """do_validate_backup — ZIP-based validation: happy path and all failure modes."""

    def _seed_zip(self, backup_dir,
                  tier="daily", date_str="2026-03-13",
                  source_type="state_encrypted", restore_path="state/immigration.md.age",
                  arc_path="state/immigration.md.age", name="immigration",
                  content=b"encrypted-content"):
        zip_path = _create_test_zip(backup_dir, tier, date_str, {
            arc_path: (content, source_type, restore_path, name),
        })
        sha256 = backup._file_sha256(zip_path)
        m = backup._load_manifest()
        m["snapshots"][f"{tier}/{date_str}.zip"] = {
            "created": f"{date_str}T00:00:00+00:00", "date": date_str, "tier": tier,
            "sha256": sha256, "size": zip_path.stat().st_size, "file_count": 1,
        }
        backup._save_manifest(m)
        return zip_path

    def test_validate_ok_happy_path(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             patch("backup._age_decrypt_bytes", return_value=_good_markdown()):
            backup.do_validate_backup()

        out = capsys.readouterr().out
        assert "\u2713" in out
        assert "valid" in out.lower()
        assert backup._load_manifest()["last_validate"] is not None

    def test_validate_fails_checksum_mismatch(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._seed_zip(backup_dir)
        zip_path.write_bytes(b"corrupted zip contents!!")

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             pytest.raises(SystemExit):
            backup.do_validate_backup()

        assert "CHECKSUM" in capsys.readouterr().out

    def test_validate_fails_missing_file_in_zip(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        tier_dir = backup_dir / "daily"
        tier_dir.mkdir(parents=True, exist_ok=True)
        zip_path = tier_dir / "2026-03-13.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("manifest.json", json.dumps({
                "artha_backup_version": "2", "created": "2026-03-13T00:00:00Z",
                "date": "2026-03-13", "tier": "daily",
                "files": {"state/immigration.md.age": {
                    "sha256": "a" * 64, "size": 100,
                    "source_type": "state_encrypted",
                    "restore_path": "state/immigration.md.age",
                    "name": "immigration",
                }},
            }))
        sha256 = backup._file_sha256(zip_path)
        m = backup._load_manifest()
        m["snapshots"]["daily/2026-03-13.zip"] = {
            "date": "2026-03-13", "tier": "daily", "sha256": sha256,
            "size": zip_path.stat().st_size, "file_count": 1,
        }
        backup._save_manifest(m)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             pytest.raises(SystemExit):
            backup.do_validate_backup()

        assert "MANIFEST COVERAGE FAIL" in capsys.readouterr().out

    def test_validate_fails_decrypt_error(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             patch("backup._age_decrypt_bytes", side_effect=RuntimeError("synthetic decrypt failure")), \
             pytest.raises(SystemExit):
            backup.do_validate_backup()

        assert "DECRYPT" in capsys.readouterr().out

    def test_validate_fails_empty_content(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             patch("backup._age_decrypt_bytes", return_value=b""), \
             pytest.raises(SystemExit):
            backup.do_validate_backup()

        assert "EMPTY" in capsys.readouterr().out

    def test_validate_fails_no_yaml_frontmatter(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             patch("backup._age_decrypt_bytes",
                   return_value=("NO FRONTMATTER HERE\n" + "word " * 50).encode()), \
             pytest.raises(SystemExit):
            backup.do_validate_backup()

        assert "YAML" in capsys.readouterr().out

    def test_validate_fails_too_short(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             patch("backup._age_decrypt_bytes", return_value=b"---\ndata: ok\n---\nfew words only"), \
             pytest.raises(SystemExit):
            backup.do_validate_backup()

        assert "SHORT" in capsys.readouterr().out.upper()

    def test_validate_domain_filter(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = _create_test_zip(backup_dir, "daily", "2026-03-13", {
            "state/immigration.md.age": (b"enc-imm", "state_encrypted", "state/immigration.md.age", "immigration"),
            "state/finance.md.age":     (b"enc-fin", "state_encrypted", "state/finance.md.age",     "finance"),
        })
        sha256 = backup._file_sha256(zip_path)
        m = backup._load_manifest()
        m["snapshots"]["daily/2026-03-13.zip"] = {
            "date": "2026-03-13", "tier": "daily", "sha256": sha256,
            "size": zip_path.stat().st_size, "file_count": 2,
        }
        backup._save_manifest(m)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             patch("backup._age_decrypt_bytes", return_value=_good_markdown()) as mock_d:
            backup.do_validate_backup(domain="immigration")
            assert mock_d.call_count == 1

    def test_validate_no_backups_exits_unsuccessfully(self, mock_backup_env, capsys):
        # Remove the pre-seeded manifest snapshots
        backup._save_manifest({"last_validate": None, "snapshots": {}})
        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             pytest.raises(SystemExit) as error:
            backup.do_validate_backup()

        assert error.value.code == 1
        assert "No backups" in capsys.readouterr().out
        assert backup._load_manifest()["last_validate_errors"] == 1

    def test_validation_never_replaces_a_corrupt_catalog_with_empty_history(
        self, mock_backup_env,
    ):
        manifest_path = foundation._config["BACKUP_MANIFEST"]
        original = b"{ broken catalog; preserve for recovery }"
        manifest_path.write_bytes(original)
        with (
            patch("backup.check_age_installed", return_value=True),
            patch("backup.get_private_key", return_value="synthetic-key"),
            pytest.raises((SystemExit, RuntimeError)),
        ):
            backup.do_validate_backup()
        assert manifest_path.read_bytes() == original

    @pytest.mark.parametrize("defect", [
        "empty_files", "missing_files", "invalid_files", "invalid_metadata",
        "catalog_count", "catalog_boolean", "extra_entry", "duplicate_entry",
        "missing_zip_checksum", "missing_file_checksum",
        "invalid_manifest", "invalid_json", "invalid_zip",
    ])
    def test_validation_requires_nonempty_complete_integrity_coverage(
        self, mock_backup_env, defect,
    ):
        zip_path = self._seed_zip(foundation._config["BACKUP_DIR"])
        with zipfile.ZipFile(zip_path) as archive:
            internal = json.loads(archive.read("manifest.json"))
        member = "state/immigration.md.age"
        if defect == "empty_files":
            internal["files"] = {}
        elif defect == "missing_files":
            del internal["files"]
        elif defect == "invalid_files":
            internal["files"] = [member]
        elif defect == "invalid_metadata":
            internal["files"][member] = None
        elif defect == "missing_file_checksum":
            del internal["files"][member]["sha256"]
        elif defect == "invalid_manifest":
            internal = []
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr(member, b"encrypted-content")
            archive.writestr(
                "manifest.json", "not-json" if defect == "invalid_json" else json.dumps(internal),
            )
            if defect == "extra_entry":
                archive.writestr("state/unlisted.md.age", b"unlisted-ciphertext")
            elif defect == "duplicate_entry":
                with pytest.warns(UserWarning, match="Duplicate name"):
                    archive.writestr(member, b"encrypted-content")
        if defect == "invalid_zip":
            zip_path.write_bytes(b"not-a-zip")
        manifest = backup._load_manifest()
        previous = "2026-01-01T00:00:00+00:00"
        manifest["last_validate"] = previous
        catalog = manifest["snapshots"]["daily/2026-03-13.zip"]
        catalog["sha256"] = backup._file_sha256(zip_path)
        if defect == "catalog_count":
            catalog["file_count"] = 2
        elif defect == "catalog_boolean":
            catalog["file_count"] = True
        elif defect == "missing_zip_checksum":
            del catalog["sha256"]
        backup._save_manifest(manifest)

        with (
            patch("backup.check_age_installed", return_value=True),
            patch("backup.get_private_key", return_value="synthetic-key"),
            patch("backup._age_decrypt_bytes", return_value=_good_markdown()) as decrypt,
            pytest.raises(SystemExit) as error,
        ):
            backup.do_validate_backup()
        decrypt.assert_not_called()
        assert error.value.code == 1
        result = backup._load_manifest()
        assert result["last_validate"] == previous
        assert result["last_validate_errors"] >= 1

    @pytest.mark.parametrize("domain,fail_one", [(None, True), ("immigration", False)])
    def test_partial_or_filtered_validation_does_not_advance_full_success(
        self, mock_backup_env, domain, fail_one,
    ):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = _create_test_zip(backup_dir, "daily", "2026-03-13", {
            "state/immigration.md.age": (b"enc-imm", "state_encrypted", "state/immigration.md.age", "immigration"),
            "state/finance.md.age": (b"enc-fin", "state_encrypted", "state/finance.md.age", "finance"),
        })
        manifest = backup._load_manifest()
        previous = "2026-01-01T00:00:00+00:00"
        manifest["last_validate"] = previous
        manifest["snapshots"]["daily/2026-03-13.zip"] = {
            "date": "2026-03-13", "tier": "daily", "sha256": backup._file_sha256(zip_path),
            "size": zip_path.stat().st_size, "file_count": 2,
        }
        backup._save_manifest(manifest)

        def decrypt(_identity, ciphertext):
            if fail_one and ciphertext == b"enc-fin":
                raise RuntimeError("synthetic decrypt failure")
            return _good_markdown()

        with (
            patch("backup.check_age_installed", return_value=True),
            patch("backup.get_private_key", return_value="synthetic-key"),
            patch("backup._age_decrypt_bytes", side_effect=decrypt),
        ):
            if fail_one:
                with pytest.raises(SystemExit) as error:
                    backup.do_validate_backup(domain=domain)
                assert error.value.code == 1
            else:
                backup.do_validate_backup(domain=domain)

        current = backup._load_manifest()
        assert current["last_validate"] == previous
        if fail_one:
            assert current["last_validate_errors"] == 1


# ---------------------------------------------------------------------------
# §6 Backup Status
# ---------------------------------------------------------------------------

class TestBackupStatus:
    def test_status_shows_never_when_no_backups(self, mock_backup_env, capsys):
        backup._save_manifest({"last_validate": None, "snapshots": {}})
        backup.do_backup_status()
        out = capsys.readouterr().out
        assert "NEVER" in out or "No backups" in out

    def test_status_shows_tiers(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        m = backup._load_manifest()
        for tier, d in [("daily", "2026-03-13"), ("weekly", "2026-03-08"),
                        ("monthly", "2026-02-28"), ("yearly", "2025-12-31")]:
            (backup_dir / tier).mkdir(parents=True, exist_ok=True)
            z = backup_dir / tier / f"{d}.zip"
            with zipfile.ZipFile(str(z), "w") as zf:
                zf.writestr("manifest.json", json.dumps({"date": d, "tier": tier, "files": {}}))
            m["snapshots"][f"{tier}/{d}.zip"] = {
                "date": d, "tier": tier, "file_count": 5,
                "sha256": "a" * 64, "size": 1024,
            }
        backup._save_manifest(m)
        backup.do_backup_status()
        out = capsys.readouterr().out
        assert "DAILY" in out
        assert "WEEKLY" in out
        assert "MONTHLY" in out
        assert "YEARLY" in out

    def test_status_warns_when_validation_overdue(self, mock_backup_env, capsys):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
        m = backup._load_manifest()
        m["last_validate"] = old_ts
        backup._save_manifest(m)
        backup.do_backup_status()
        out = capsys.readouterr().out
        assert "overdue" in out.lower() or "\u26a0" in out


# ---------------------------------------------------------------------------
# §7 Backup Registry
# ---------------------------------------------------------------------------

class TestBackupRegistry:
    """load_backup_registry reads user_profile.yaml and returns correct entries."""

    def _write_profile(self, config_dir, state_files, config_files=None):
        import yaml as _yaml
        profile = {"backup": {"state_files": state_files, "config_files": config_files or []}}
        profile_path = config_dir / "user_profile.yaml"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(_yaml.dump(profile), encoding="utf-8")

    def test_loads_sensitive_state_entries(self, mock_backup_env):
        config_dir = foundation._config["CONFIG_DIR"]
        state_dir  = foundation._config["STATE_DIR"]
        self._write_profile(config_dir, [
            {"name": "finance", "sensitive": True},
            {"name": "health", "sensitive": True},
        ])
        entries = backup.load_backup_registry()
        names = {e["name"] for e in entries}
        assert "finance" in names
        assert "health" in names
        for e in entries:
            assert e["source_type"] == "state_encrypted"
            assert str(e["source_path"]).endswith(".md.age")
            assert e["restore_path"].endswith(".md.age")

    def test_loads_plain_state_entries(self, mock_backup_env):
        config_dir = foundation._config["CONFIG_DIR"]
        self._write_profile(config_dir, [
            {"name": "goals", "sensitive": False},
            {"name": "home", "sensitive": False},
        ])
        entries = backup.load_backup_registry()
        for e in entries:
            assert e["source_type"] == "state_plain"
            assert str(e["source_path"]).endswith(".md")
            assert e["restore_path"] == f"state/{e['name']}.md"

    def test_loads_config_entries(self, mock_backup_env):
        config_dir = foundation._config["CONFIG_DIR"]
        self._write_profile(config_dir, [], ["config/user_profile.yaml", "config/routing.yaml"])
        entries = backup.load_backup_registry()
        names = {e["name"] for e in entries}
        assert "cfg__config__user_profile_yaml" in names
        assert "cfg__config__routing_yaml" in names
        for e in entries:
            assert e["source_type"] == "config"
            assert e["restore_path"].startswith("config/")

    def test_fallback_when_no_profile(self, mock_backup_env):
        # No user_profile.yaml — only settings.md from fixture → fallback to SENSITIVE_FILES
        entries = backup.load_backup_registry()
        entry_names = {e["name"] for e in entries}
        for domain, _ext in foundation._normalize_sensitive_files(foundation._config["SENSITIVE_FILES"]):
            assert domain in entry_names
        for e in entries:
            assert e["source_type"] == "state_encrypted"

    def test_fallback_when_no_backup_section(self, mock_backup_env):
        import yaml as _yaml
        config_dir = foundation._config["CONFIG_DIR"]
        profile_path = config_dir / "user_profile.yaml"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(_yaml.dump({"family": {"name": "Test"}}), encoding="utf-8")
        entries = backup.load_backup_registry()
        entry_names = {e["name"] for e in entries}
        for domain, _ext in foundation._normalize_sensitive_files(foundation._config["SENSITIVE_FILES"]):
            assert domain in entry_names

    def test_all_31_state_files_present_in_full_registry(self, mock_backup_env):
        """A full registry with 31 state + 4 config entries is loaded correctly."""
        config_dir = foundation._config["CONFIG_DIR"]
        state_files = (
            [{"name": n, "sensitive": True}
             for n in ["immigration", "finance", "insurance", "estate", "health",
                       "vehicle", "contacts", "occasions", "audit"]] +
            [{"name": n, "sensitive": False}
             for n in ["boundary", "calendar", "comms", "dashboard", "decisions",
                       "digital", "employment", "goals", "health-check",
                       "health-metrics", "home", "kids", "learning", "memory",
                       "onenote_progress", "open_items", "scenarios", "shopping",
                       "social", "travel", "work-calendar", "plaid-privacy-research"]]
        )
        config_files = [
            "config/user_profile.yaml", "config/routing.yaml",
            "config/connectors.yaml",   "config/artha_config.yaml",
        ]
        self._write_profile(config_dir, state_files, config_files)
        entries = backup.load_backup_registry()
        assert len(entries) == len(state_files) + len(config_files)
        sensitive_count = sum(1 for e in entries if e["source_type"] == "state_encrypted")
        plain_count     = sum(1 for e in entries if e["source_type"] == "state_plain")
        config_count    = sum(1 for e in entries if e["source_type"] == "config")
        assert sensitive_count == 9
        assert plain_count     == 22
        assert config_count    == 4


# ---------------------------------------------------------------------------
# §8 Snapshot: plain state + config file handling
# ---------------------------------------------------------------------------

class TestBackupSnapshotComprehensive:
    """backup_snapshot encrypts plain/config on-the-fly and stores them inside the ZIP."""

    def _write_profile(self, config_dir, state_files, config_files=None):
        import yaml as _yaml
        profile = {"backup": {"state_files": state_files, "config_files": config_files or []}}
        p = config_dir / "user_profile.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_yaml.dump(profile), encoding="utf-8")

    def test_plain_state_file_is_encrypted_into_backup(self, mock_backup_env):
        config_dir = foundation._config["CONFIG_DIR"]
        state_dir  = foundation._config["STATE_DIR"]
        (state_dir / "goals.md").write_text("---\n# Goals\nword " * 40)
        today = date(2026, 3, 9)
        registry = [
            {"name": "goals", "source_type": "state_plain",
             "source_path": state_dir / "goals.md",
             "restore_path": "state/goals.md"},
        ]

        def fake_encrypt(pk, src, dst):
            dst.write_bytes(b"encrypted-goals")
            return True

        with patch("backup.get_public_key", return_value="age1mock"), \
             patch("backup.age_encrypt", side_effect=fake_encrypt):
            count = backup.backup_snapshot(registry, today=today)

        assert count == 1
        zip_path = foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip"
        assert zip_path.exists()
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        assert "state/goals.md" in internal["files"]
        meta = internal["files"]["state/goals.md"]
        assert meta["source_type"] == "state_plain"
        assert meta["restore_path"] == "state/goals.md"
        assert meta["data"]["entry"].startswith("payload/")

    def test_config_file_is_encrypted_into_backup(self, mock_backup_env):
        config_dir = foundation._config["CONFIG_DIR"]
        artha_dir  = foundation._config["ARTHA_DIR"]
        (config_dir / "artha_config.yaml").write_text("todo_lists:\n  general: abc123\n")
        today = date(2026, 3, 9)
        registry = [
            {"name": "cfg__config__artha_config_yaml", "source_type": "config",
             "source_path": config_dir / "artha_config.yaml",
             "restore_path": "config/artha_config.yaml"},
        ]

        def fake_encrypt(pk, src, dst):
            dst.write_bytes(b"encrypted-config")
            return True

        with patch("backup.get_public_key", return_value="age1mock"), \
             patch("backup.age_encrypt", side_effect=fake_encrypt):
            count = backup.backup_snapshot(registry, today=today)

        assert count == 1
        zip_path = foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip"
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        assert "config/artha_config.yaml" in internal["files"]
        meta = internal["files"]["config/artha_config.yaml"]
        assert meta["source_type"] == "config"
        assert meta["restore_path"] == "config/artha_config.yaml"

    def test_plain_file_skipped_when_no_pubkey(self, mock_backup_env):
        state_dir = foundation._config["STATE_DIR"]
        (state_dir / "goals.md").write_text("---\n# Goals\n")
        registry = [
            {"name": "goals", "source_type": "state_plain",
             "source_path": state_dir / "goals.md",
             "restore_path": "state/goals.md"},
        ]
        with patch("backup.get_public_key", side_effect=SystemExit(1)):
            count = backup.backup_snapshot(registry, today=date(2026, 3, 9))
        assert count == 0

    def test_mixed_registry_backs_up_all_types(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        (state_dir / "finance.md.age").write_bytes(_fake_age_bytes("finance"))
        (state_dir / "goals.md").write_text("---\n# Goals\n")
        (config_dir / "artha_config.yaml").write_text("todo_lists:\n  general: abc\n")

        registry = [
            {"name": "finance", "source_type": "state_encrypted",
             "source_path": state_dir / "finance.md.age",
             "restore_path": "state/finance.md.age"},
            {"name": "goals", "source_type": "state_plain",
             "source_path": state_dir / "goals.md",
             "restore_path": "state/goals.md"},
            {"name": "cfg__config__artha_config_yaml", "source_type": "config",
             "source_path": config_dir / "artha_config.yaml",
             "restore_path": "config/artha_config.yaml"},
        ]

        def fake_encrypt(pk, src, dst):
            dst.write_bytes(b"encrypted")
            return True

        with patch("backup.get_public_key", return_value="age1mock"), \
             patch("backup.age_encrypt", side_effect=fake_encrypt):
            count = backup.backup_snapshot(registry, today=date(2026, 3, 9))

        assert count == 3
        zip_path = foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip"
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        assert "state/finance.md.age" in internal["files"]
        assert "state/goals.md" in internal["files"]
        assert "config/artha_config.yaml" in internal["files"]

    def test_manifest_entry_has_restore_path_and_source_type(self, mock_backup_env):
        state_dir = foundation._config["STATE_DIR"]
        (state_dir / "immigration.md.age").write_bytes(_fake_age_bytes("immigration"))
        registry = [
            {"name": "immigration", "source_type": "state_encrypted",
             "source_path": state_dir / "immigration.md.age",
             "restore_path": "state/immigration.md.age"},
        ]
        backup.backup_snapshot(registry, today=date(2026, 3, 9))
        m = backup._load_manifest()
        key = "daily/2026-03-09.zip"
        assert key in m["snapshots"]
        zip_path = foundation._config["BACKUP_DIR"] / key
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        meta = internal["files"]["state/immigration.md.age"]
        assert meta["source_type"]  == "state_encrypted"
        assert meta["restore_path"] == "state/immigration.md.age"


# ---------------------------------------------------------------------------
# §9 Restore (catalog-based)
# ---------------------------------------------------------------------------

class TestRestore:
    """do_restore finds the right ZIP from the catalog and reconstructs files."""

    def _seed_zip(self, backup_dir, tier="daily", date_str="2026-03-14"):
        files_dict = {
            "state/immigration.md.age": (b"enc-imm", "state_encrypted", "state/immigration.md.age", "immigration"),
            "state/goals.md.age":       (b"enc-goals", "state_plain",    "state/goals.md",           "goals"),
            "config/user_profile.yaml.age": (b"enc-cfg", "config",       "config/user_profile.yaml", "cfg_user_profile"),
        }
        zip_path = _create_test_zip(backup_dir, tier, date_str, files_dict)
        sha256 = backup._file_sha256(zip_path)
        m = backup._load_manifest()
        m["snapshots"][f"{tier}/{date_str}.zip"] = {
            "created": f"{date_str}T00:00:00+00:00", "date": date_str, "tier": tier,
            "sha256": sha256, "size": zip_path.stat().st_size, "file_count": 3,
        }
        backup._save_manifest(m)
        return zip_path

    def test_restore_dry_run_lists_files_without_writing(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)
        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup.age_decrypt", return_value=True):
            backup.do_restore(date_str="2026-03-14", dry_run=True)

        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "state/immigration.md.age" in out
        assert not (foundation._config["STATE_DIR"] / "immigration.md.age").exists()

    def test_restore_state_encrypted_copies_age_file(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)
        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", side_effect=[
                 _good_markdown(), _good_markdown(), b"age_recipient: age1xxx\n",
             ]):
            backup.do_restore(date_str="2026-03-14", confirm=True)

        restored = foundation._config["STATE_DIR"] / "immigration.md.age"
        assert restored.exists()
        assert restored.read_bytes() == b"enc-imm"

    def test_restore_state_plain_decrypts_file(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", side_effect=[
                 _good_markdown(), _good_markdown(), b"age_recipient: age1xxx\n",
             ]):
            backup.do_restore(date_str="2026-03-14", confirm=True)

        restored = foundation._config["STATE_DIR"] / "goals.md"
        assert restored.exists()
        assert restored.read_bytes() == _good_markdown()

    def test_restore_config_decrypts_to_config_dir(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", side_effect=[
                 _good_markdown(), _good_markdown(), b"age_recipient: age1xxx\n",
             ]):
            backup.do_restore(date_str="2026-03-14", confirm=True)

        restored = foundation._config["CONFIG_DIR"] / "user_profile.yaml"
        assert restored.exists()
        assert "age1xxx" in restored.read_text()

    def test_restore_fails_on_checksum_mismatch(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        tier_dir = backup_dir / "daily"
        tier_dir.mkdir(parents=True, exist_ok=True)
        zip_path = tier_dir / "2026-03-14.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("state/immigration.md.age", b"correct-content")
            zf.writestr("manifest.json", json.dumps({
                "artha_backup_version": "2", "created": "2026-03-14T00:00:00Z",
                "date": "2026-03-14", "tier": "daily",
                "files": {"state/immigration.md.age": {
                    "sha256": "a" * 64, "size": 15,
                    "source_type": "state_encrypted",
                    "restore_path": "state/immigration.md.age", "name": "immigration",
                }},
            }))
        sha256 = backup._file_sha256(zip_path)
        m = backup._load_manifest()
        m["snapshots"]["daily/2026-03-14.zip"] = {
            "date": "2026-03-14", "tier": "daily", "sha256": sha256,
            "size": zip_path.stat().st_size, "file_count": 1,
        }
        backup._save_manifest(m)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             pytest.raises(SystemExit):
            backup.do_restore(date_str="2026-03-14", confirm=True)

        captured = capsys.readouterr()
        assert "CHECKSUM" in captured.out + captured.err

    def test_restore_no_backups_exits_gracefully(self, mock_backup_env, capsys):
        backup._save_manifest({"last_validate": None, "snapshots": {}})
        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"):
            backup.do_restore(date_str="2026-03-14")

        assert "No backups" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# §10 Install (explicit ZIP)
# ---------------------------------------------------------------------------

class TestInstall:
    """do_install restores from an explicit ZIP file (cold-start)."""

    def _make_zip(self, tmp_path):
        files_dict = {
            "state/immigration.md.age": (b"enc-imm", "state_encrypted", "state/immigration.md.age", "immigration"),
            "state/goals.md.age":       (b"enc-goals", "state_plain",    "state/goals.md",           "goals"),
            "config/artha_config.yaml.age": (b"enc-cfg", "config",       "config/artha_config.yaml", "artha_config"),
        }
        tier_dir = tmp_path / "export"
        return _create_test_zip(tier_dir, "daily", "2026-03-14", files_dict)

    def test_install_dry_run_previews_without_writing(self, mock_backup_env, tmp_path, capsys):
        zip_path = self._make_zip(tmp_path)
        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup.age_decrypt", return_value=True):
            backup.do_install(str(zip_path), dry_run=True)

        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert not (foundation._config["STATE_DIR"] / "immigration.md.age").exists()

    def test_install_state_encrypted_copied_directly(self, mock_backup_env, tmp_path):
        zip_path = self._make_zip(tmp_path)
        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", side_effect=[
                 _good_markdown(), _good_markdown(), b"todo_lists:\n  general: id123\n",
             ]):
            backup.do_install(str(zip_path), confirm=True)

        restored = foundation._config["STATE_DIR"] / "immigration.md.age"
        assert restored.exists()
        assert restored.read_bytes() == b"enc-imm"

    def test_install_state_plain_decrypted(self, mock_backup_env, tmp_path):
        zip_path = self._make_zip(tmp_path)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", side_effect=[
                 _good_markdown(), _good_markdown(), b"todo_lists:\n  general: id123\n",
             ]):
            backup.do_install(str(zip_path), confirm=True)

        restored = foundation._config["STATE_DIR"] / "goals.md"
        assert restored.read_bytes() == _good_markdown()

    def test_install_config_decrypted_to_config_dir(self, mock_backup_env, tmp_path):
        zip_path = self._make_zip(tmp_path)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", side_effect=[
                 _good_markdown(), _good_markdown(), b"todo_lists:\n  general: id123\n",
             ]):
            backup.do_install(str(zip_path), confirm=True)

        restored = foundation._config["CONFIG_DIR"] / "artha_config.yaml"
        assert "id123" in restored.read_text()

    def test_install_missing_zip_exits(self, mock_backup_env):
        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             pytest.raises(SystemExit):
            backup.do_install("/nonexistent/path/2026-03-14.zip")


# ---------------------------------------------------------------------------
# §11 --data-only mode
# ---------------------------------------------------------------------------

class TestDataOnlyRestore:
    """--data-only flag skips config source_type entries in both restore and install."""

    def _make_full_zip(self, backup_dir):
        files_dict = {
            "state/immigration.md.age": (b"enc-imm",   "state_encrypted", "state/immigration.md.age",  "immigration"),
            "state/goals.md.age":       (b"enc-goals",  "state_plain",    "state/goals.md",             "goals"),
            "config/routing.yaml.age":  (b"enc-cfg",    "config",         "config/routing.yaml",        "routing"),
        }
        return _create_test_zip(backup_dir, "daily", "2026-03-14", files_dict)

    def _register_zip(self, zip_path, tier="daily", date_str="2026-03-14"):
        sha256 = backup._file_sha256(zip_path)
        m = backup._load_manifest()
        m["snapshots"][f"{tier}/{date_str}.zip"] = {
            "created": f"{date_str}T00:00:00+00:00", "date": date_str, "tier": tier,
            "sha256": sha256, "size": zip_path.stat().st_size, "file_count": 3,
        }
        backup._save_manifest(m)

    def test_restore_data_only_skips_config(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._make_full_zip(backup_dir)
        self._register_zip(zip_path)

        def fake_decrypt(_identity, ciphertext):
            return b"route: sample\n" if ciphertext == b"enc-cfg" else _good_markdown()

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", side_effect=fake_decrypt):
            backup.do_restore(date_str="2026-03-14", data_only=True, confirm=True)

        assert (foundation._config["STATE_DIR"] / "immigration.md.age").exists()
        assert (foundation._config["STATE_DIR"] / "goals.md").exists()
        assert not (foundation._config["CONFIG_DIR"] / "routing.yaml").exists()

    def test_restore_full_includes_config(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._make_full_zip(backup_dir)
        self._register_zip(zip_path)

        def fake_decrypt(_identity, ciphertext):
            return b"route: sample\n" if ciphertext == b"enc-cfg" else _good_markdown()

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", side_effect=fake_decrypt):
            backup.do_restore(date_str="2026-03-14", confirm=True)

        assert (foundation._config["STATE_DIR"] / "immigration.md.age").exists()
        assert (foundation._config["CONFIG_DIR"] / "routing.yaml").exists()

    def test_restore_data_only_dry_run_shows_scope(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._make_full_zip(backup_dir)
        self._register_zip(zip_path)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", return_value=_good_markdown()):
            backup.do_restore(date_str="2026-03-14", dry_run=True, data_only=True)

        out = capsys.readouterr().out
        assert "state only" in out.lower()
        assert "config skipped" in out.lower()

    def test_install_data_only_skips_config(self, mock_backup_env, tmp_path):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._make_full_zip(tmp_path)

        def fake_decrypt(_identity, ciphertext):
            return b"route: sample\n" if ciphertext == b"enc-cfg" else _good_markdown()

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", side_effect=fake_decrypt):
            backup.do_install(str(zip_path), data_only=True, confirm=True)

        assert (foundation._config["STATE_DIR"] / "immigration.md.age").exists()
        assert (foundation._config["STATE_DIR"] / "goals.md").exists()
        assert not (foundation._config["CONFIG_DIR"] / "routing.yaml").exists()

    def test_install_full_includes_config(self, mock_backup_env, tmp_path):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._make_full_zip(tmp_path)

        def fake_decrypt(_identity, ciphertext):
            return b"route: sample\n" if ciphertext == b"enc-cfg" else _good_markdown()

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", side_effect=fake_decrypt):
            backup.do_install(str(zip_path), confirm=True)

        assert (foundation._config["CONFIG_DIR"] / "routing.yaml").exists()


# ---------------------------------------------------------------------------
# §12 Structural guards
# ---------------------------------------------------------------------------

def test_no_circular_import():
    """All three modules can be imported in any order without ImportError."""
    import importlib
    # reload in both orders to verify no circular dependency
    importlib.reload(importlib.import_module("foundation"))
    importlib.reload(importlib.import_module("vault"))
    importlib.reload(importlib.import_module("backup"))


def test_config_propagates_to_all_modules(mock_backup_env):
    """foundation._config is the same object used by backup._config."""
    # backup.py does `from scripts.foundation import _config`
    # so backup._config IS foundation._config (same object reference)
    assert foundation._config is backup._config
    assert foundation._config["ARTHA_DIR"] == backup._config["ARTHA_DIR"]


def test_vault_has_no_backup_functions():
    """backup functions have not been removed from vault.py yet — this test
    is a placeholder that will be updated in Step 4 (removal phase).
    After Step 4, this will verify vault has NO backup functions.
    For now we just verify backup.py has the correct functions."""
    assert callable(backup.backup_snapshot)
    assert callable(backup.load_backup_registry)
    assert callable(backup.do_restore)
    assert callable(backup.do_install)
    assert callable(backup.do_validate_backup)
    assert callable(backup.get_health_summary)


# ---------------------------------------------------------------------------
# §13 Standalone CLI tests
# ---------------------------------------------------------------------------

class TestBackupCLI:
    def test_status_subcommand(self, mock_backup_env, capsys):
        """backup.do_backup_status() runs without error."""
        backup.do_backup_status()
        out = capsys.readouterr().out
        assert "VAULT BACKUP STATUS" in out

    def test_help_output(self):
        """backup.py --help shows all required subcommands."""
        result = subprocess.run(
            [sys.executable, "scripts/backup.py", "--help"],
            capture_output=True, text=True,
            cwd=str(foundation._config["ARTHA_DIR"]),
            env={**os.environ, "ARTHA_NO_REEXEC": "1"},
        )
        assert result.returncode == 0
        assert "status" in result.stdout
        assert "restore" in result.stdout
        assert "snapshot" in result.stdout
        assert "export-key" in result.stdout
        assert "import-key" in result.stdout
        assert "preflight" in result.stdout

    def test_snapshot_subcommand_via_main(self, mock_backup_env):
        """backup.main(['snapshot']) creates a ZIP when .age files exist."""
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        (state_dir / "finance.md.age").write_bytes(_fake_age_bytes("finance"))

        import yaml as _yaml
        profile = {"backup": {"state_files": [{"name": "finance", "sensitive": True}]}}
        (config_dir / "user_profile.yaml").write_text(_yaml.dump(profile), encoding="utf-8")

        backup.main(["snapshot"])
        backup_dir = foundation._config["BACKUP_DIR"]
        zips = list(backup_dir.rglob("*.zip"))
        assert len(zips) >= 1


# ---------------------------------------------------------------------------
# §14 Key management
# ---------------------------------------------------------------------------

class TestKeyManagement:
    def test_export_key_prints_key(self, mock_backup_env, capsys):
        with patch("keyring.get_password", return_value="AGE-SECRET-KEY-1FAKE"):
            backup.do_export_key()
        out = capsys.readouterr().out
        assert "AGE-SECRET-KEY-1FAKE" in out
        assert "EXTREME CARE" in out

    def test_export_key_fails_when_no_key(self):
        with patch("keyring.get_password", return_value=None), \
             pytest.raises(SystemExit):
            backup.do_export_key()

    def test_import_key_stores_valid_key(self, monkeypatch):
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO("AGE-SECRET-KEY-1FAKE\n"))
        with patch("keyring.set_password") as mock_set:
            backup.do_import_key()
        mock_set.assert_called_once()
        args = mock_set.call_args[0]
        assert args[0] == "age-key"
        assert args[1] == "artha"
        assert args[2] == "AGE-SECRET-KEY-1FAKE"

    def test_import_key_rejects_invalid_format(self, monkeypatch):
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO("not-a-valid-key\n"))
        with pytest.raises(SystemExit):
            backup.do_import_key()


# ---------------------------------------------------------------------------
# §15 Domain checksums in manifest (#6)
# ---------------------------------------------------------------------------

class TestDomainChecksums:
    """backup_snapshot stores per-domain checksums in the outer manifest."""

    def test_snapshot_records_domain_checksums(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        (state_dir / "finance.md.age").write_bytes(_fake_age_bytes("finance"))

        import yaml as _yaml
        profile = {"backup": {"state_files": [{"name": "finance", "sensitive": True}]}}
        (config_dir / "user_profile.yaml").write_text(_yaml.dump(profile), encoding="utf-8")

        registry = backup.load_backup_registry()
        count = backup.backup_snapshot(registry)
        assert count > 0

        m = backup._load_manifest()
        snap = list(m["snapshots"].values())[0]
        assert "domain_checksums" in snap
        assert "finance" in snap["domain_checksums"]
        assert len(snap["domain_checksums"]["finance"]) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# §16 Prune protection (#6)
# ---------------------------------------------------------------------------

class TestPruneProtection:
    """_prune_backups pins ZIPs that are the sole carrier of a domain checksum."""

    def test_prune_protects_sole_carrier(self, mock_backup_env):
        """A ZIP is not pruned if it contains a domain checksum not found elsewhere."""
        backup_dir = foundation._config["BACKUP_DIR"]
        state_dir  = foundation._config["STATE_DIR"]

        # Create 3 daily ZIPs: day1 has unique content, day2,3 have same content
        unique_content = _fake_age_bytes("unique-day1")
        common_content = _fake_age_bytes("common")

        m = backup._load_manifest()
        for i, (day, content) in enumerate([
            ("2026-03-01", unique_content),
            ("2026-03-02", common_content),
            ("2026-03-03", common_content),
        ]):
            zip_path = _create_test_zip(
                backup_dir, "daily", day,
                {"state/finance.md.age": (content, "state_encrypted", "state/finance.md.age", "finance")},
            )
            sha = backup._file_sha256(zip_path)
            m["snapshots"][f"daily/{day}.zip"] = {
                "date": day, "tier": "daily", "sha256": sha,
                "size": zip_path.stat().st_size, "file_count": 1,
                "domain_checksums": {"finance": hashlib.sha256(content).hexdigest()},
            }
        backup._save_manifest(m)

        # Prune with keep_n=2: day1 is candidate for deletion
        backup._prune_backups("daily", 2)

        # day1 should be PINNED (sole carrier of unique checksum)
        assert (backup_dir / "daily" / "2026-03-01.zip").exists()
        # All 3 ZIPs still exist (day1 pinned + day2,3 retained)
        zips = sorted((backup_dir / "daily").glob("*.zip"))
        assert len(zips) == 3

    def test_prune_deletes_when_checksum_duplicated(self, mock_backup_env):
        """A ZIP is pruned if all its domain checksums exist in other retained ZIPs."""
        backup_dir = foundation._config["BACKUP_DIR"]
        common = _fake_age_bytes("same")

        m = backup._load_manifest()
        for day in ("2026-03-01", "2026-03-02", "2026-03-03"):
            zip_path = _create_test_zip(
                backup_dir, "daily", day,
                {"state/finance.md.age": (common, "state_encrypted", "state/finance.md.age", "finance")},
            )
            sha = backup._file_sha256(zip_path)
            m["snapshots"][f"daily/{day}.zip"] = {
                "date": day, "tier": "daily", "sha256": sha,
                "size": zip_path.stat().st_size, "file_count": 1,
                "domain_checksums": {"finance": hashlib.sha256(common).hexdigest()},
            }
        backup._save_manifest(m)

        backup._prune_backups("daily", 2)

        # day1 should be deleted (checksum duplicated in day2 and day3)
        assert not (backup_dir / "daily" / "2026-03-01.zip").exists()
        zips = sorted((backup_dir / "daily").glob("*.zip"))
        assert len(zips) == 2


# ---------------------------------------------------------------------------
# §17 Pre-restore backup (#7)
# ---------------------------------------------------------------------------

class TestPreRestoreBackup:
    """Restore creates a pre-restore backup of live files before overwriting."""

    def test_pre_restore_backup_created(self, mock_backup_env, capsys):
        """Live files are backed up to backups/pre-restore/ before restore writes."""
        backup_dir = foundation._config["BACKUP_DIR"]
        state_dir  = foundation._config["STATE_DIR"]

        # Create a live file that will be overwritten
        (state_dir / "immigration.md.age").write_bytes(_fake_age_bytes("live"))

        # Create a backup ZIP to restore from
        zip_path = _create_test_zip(
            backup_dir, "daily", "2026-03-14",
            {"state/immigration.md.age": (
                _fake_age_bytes("backup"), "state_encrypted",
                "state/immigration.md.age", "immigration",
            )},
        )
        sha = backup._file_sha256(zip_path)
        m = backup._load_manifest()
        m["snapshots"]["daily/2026-03-14.zip"] = {
            "date": "2026-03-14", "tier": "daily", "sha256": sha,
            "size": zip_path.stat().st_size, "file_count": 1,
        }
        backup._save_manifest(m)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"), \
             patch("backup._age_decrypt_bytes", return_value=_good_markdown()):
            backup.do_restore(date_str="2026-03-14", confirm=True)

        # Pre-restore backup directory should exist with the live file
        pre_restore = backup_dir / "pre-restore"
        assert pre_restore.exists()
        backed_files = list(pre_restore.iterdir())
        assert len(backed_files) >= 1


# ---------------------------------------------------------------------------
# §18 Confirm gate (#7)
# ---------------------------------------------------------------------------

class TestConfirmGate:
    """Restore requires --confirm to write files (safety gate)."""

    def test_restore_without_confirm_shows_preview(self, mock_backup_env, capsys):
        """Without --confirm, restore shows preview without writing."""
        backup_dir = foundation._config["BACKUP_DIR"]
        state_dir  = foundation._config["STATE_DIR"]

        zip_path = _create_test_zip(
            backup_dir, "daily", "2026-03-14",
            {"state/immigration.md.age": (
                b"enc-imm", "state_encrypted",
                "state/immigration.md.age", "immigration",
            )},
        )
        sha = backup._file_sha256(zip_path)
        m = backup._load_manifest()
        m["snapshots"]["daily/2026-03-14.zip"] = {
            "date": "2026-03-14", "tier": "daily", "sha256": sha,
            "size": zip_path.stat().st_size, "file_count": 1,
        }
        backup._save_manifest(m)

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"):
            backup.do_restore(date_str="2026-03-14")  # no confirm

        out = capsys.readouterr().out
        assert "PREVIEW" in out
        assert "--confirm" in out
        assert not (state_dir / "immigration.md.age").exists()

    def test_install_without_confirm_shows_preview(self, mock_backup_env, tmp_path, capsys):
        """Install without --confirm shows preview."""
        zip_path = _create_test_zip(
            tmp_path / "export", "daily", "2026-03-14",
            {"state/immigration.md.age": (
                b"enc-imm", "state_encrypted",
                "state/immigration.md.age", "immigration",
            )},
        )

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-priv"):
            backup.do_install(str(zip_path))  # no confirm

        out = capsys.readouterr().out
        assert "PREVIEW" in out
        assert "--confirm" in out


# ---------------------------------------------------------------------------
# §19 Key export timestamp (#3)
# ---------------------------------------------------------------------------

class TestKeyExportTimestamp:
    """export-key records a timestamp in the manifest for health check."""

    def test_export_records_timestamp(self, mock_backup_env):
        with patch("keyring.get_password", return_value="AGE-SECRET-KEY-1FAKE"):
            backup.do_export_key()

        m = backup._load_manifest()
        assert "last_key_export" in m
        assert m["last_key_export"].startswith("20")


class TestGFSVersion3:
    def test_snapshot_uses_named_encrypted_payloads_and_preserves_state_age_bytes(
        self, mock_backup_env,
    ):
        state_dir = foundation._config["STATE_DIR"]
        source = state_dir / "private.md.age"
        source.write_bytes(_fake_age_bytes("already-encrypted"))
        registry = [{
            "name": "private",
            "source_type": "state_encrypted",
            "source_path": source,
            "restore_path": "state/private.md.age",
        }]
        encrypted_streams = []

        def encrypt_bytes(recipient, data):
            encrypted_streams.append(data)
            return b"age-ciphertext:" + data

        with (
            patch("backup.get_public_key", return_value="age1synthetic"),
            patch("backup._read_named_streams", return_value={
                "Zone.Identifier": b"label-bytes",
                "Empty": b"",
            }),
            patch("backup._age_encrypt_bytes", side_effect=encrypt_bytes),
        ):
            assert backup.backup_snapshot(registry, today=date(2026, 3, 9)) == 1

        archive_path = foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip"
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["artha_backup_version"] == "3"
            assert set(manifest["files"]) == {"state/private.md.age"}
            item = manifest["files"]["state/private.md.age"]
            assert item["restore_path"] == "state/private.md.age"
            assert item["data"]["entry"].startswith("payload/")
            assert archive.read(item["data"]["entry"]) == source.read_bytes()
            assert item["streams"]["Zone.Identifier"]["entry"].startswith("payload/")
            assert item["streams"]["Empty"]["size"] == len(b"age-ciphertext:")
            assert all(name.startswith("payload/") for name in archive.namelist()
                       if name != "manifest.json")
        assert encrypted_streams == [b"label-bytes", b""]
        catalog = backup._load_manifest()["snapshots"]["daily/2026-03-09.zip"]
        assert catalog["file_count"] == 1
        assert catalog["stream_count"] == 2

    def test_local_database_snapshot_includes_uncheckpointed_wal_rows(
        self, mock_backup_env,
    ):
        import sqlite3 as sqlite

        source = foundation._config["ARTHA_LOCAL_DIR"] / "wal.db"
        source.parent.mkdir(parents=True, exist_ok=True)
        writer = sqlite.connect(source)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE sample (value TEXT)")
        writer.execute("INSERT INTO sample VALUES ('committed-in-wal')")
        writer.commit()
        assert Path(str(source) + "-wal").exists()
        captured = []

        def encrypt_bytes(recipient, data):
            captured.append(data)
            return b"ciphertext"

        registry = [{
            "name": "localdb__wal",
            "source_type": "local_db",
            "source_path": source,
            "restore_path": ".artha-local/wal.db",
        }]
        try:
            with (
                patch("backup.get_public_key", return_value="age1synthetic"),
                patch("backup._read_named_streams", return_value={}),
                patch("backup._age_encrypt_bytes", side_effect=encrypt_bytes),
            ):
                assert backup.backup_snapshot(registry, today=date(2026, 3, 9)) == 1
        finally:
            writer.close()

        assert len(captured) == 1
        image = sqlite.connect(":memory:")
        image.deserialize(captured[0])
        assert image.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert image.execute("SELECT value FROM sample").fetchone() == ("committed-in-wal",)
        image.close()

    def test_v3_validation_is_in_memory_and_accepts_empty_file_and_stream(
        self, mock_backup_env,
    ):
        _create_v3_zip(foundation._config["BACKUP_DIR"], "2026-03-13", {
            "config/empty.yaml": (
                "config", "empty_config", b"encrypted-empty-file",
                {"Empty": b"encrypted-empty-stream"},
            ),
        })

        with (
            patch("backup.check_age_installed", return_value=True),
            patch("backup.get_private_key", return_value="synthetic-key"),
            patch("backup.age_decrypt", side_effect=AssertionError("file-based decrypt used")),
            patch("backup._age_decrypt_bytes", side_effect=[
                b"", b"",
            ]) as decrypt,
            patch("backup.tempfile.TemporaryDirectory",
                  side_effect=AssertionError("plaintext validation directory created")),
        ):
            backup.do_validate_backup()

        assert decrypt.call_count == 2
        assert backup._load_manifest()["last_validate"] is not None

    @pytest.mark.parametrize(
        "defect",
        [
            "unsupported_version", "unlisted_payload", "bad_hash",
            "bad_stream_hash", "stream_decrypt_failure",
        ],
    )
    def test_v3_validation_rejects_bad_archive_before_decrypt(
        self, mock_backup_env, defect,
    ):
        archive_path = _create_v3_zip(foundation._config["BACKUP_DIR"], "2026-03-13", {
            "state/example.md.age": (
                "state_encrypted", "example", b"synthetic-age",
                {"Zone.Identifier": b"synthetic-stream"},
            ),
        })
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            payload = archive.read(manifest["files"]["state/example.md.age"]["data"]["entry"])
        if defect == "unsupported_version":
            manifest["artha_backup_version"] = "99"
        elif defect == "bad_hash":
            manifest["files"]["state/example.md.age"]["data"]["sha256"] = "0" * 64
        elif defect == "bad_stream_hash":
            manifest["files"]["state/example.md.age"]["streams"]["Zone.Identifier"]["sha256"] = "0" * 64
        with zipfile.ZipFile(archive_path, "w") as archive:
            entry = manifest["files"]["state/example.md.age"]["data"]["entry"]
            archive.writestr(entry, payload)
            stream = manifest["files"]["state/example.md.age"]["streams"]["Zone.Identifier"]
            archive.writestr(stream["entry"], b"synthetic-stream")
            if defect == "unlisted_payload":
                archive.writestr("payload/unlisted.age", b"unlisted")
            archive.writestr("manifest.json", json.dumps(manifest))
        catalog = backup._load_manifest()
        catalog["snapshots"]["daily/2026-03-13.zip"]["sha256"] = backup._file_sha256(archive_path)
        previous = "2026-01-01T00:00:00+00:00"
        catalog["last_validate"] = previous
        backup._save_manifest(catalog)

        with (
            patch("backup.check_age_installed", return_value=True),
            patch("backup.get_private_key", return_value="synthetic-key"),
            patch(
                "backup._age_decrypt_bytes",
                side_effect=(
                    [_good_markdown(), RuntimeError("synthetic stream decrypt failure")]
                    if defect == "stream_decrypt_failure" else None
                ),
                return_value=_good_markdown() if defect == "bad_stream_hash" else None,
            ) as decrypt,
            pytest.raises(SystemExit) as error,
        ):
            backup.do_validate_backup()

        assert error.value.code == 1
        if defect == "stream_decrypt_failure":
            assert decrypt.call_count == 2
        elif defect == "bad_stream_hash":
            assert decrypt.call_count == 1
        else:
            decrypt.assert_not_called()
        assert backup._load_manifest()["last_validate"] == previous

    def test_stream_restore_refuses_unsupported_host_before_any_write(
        self, mock_backup_env, tmp_path,
    ):
        archive_path = _create_v3_zip(tmp_path / "source", "2026-03-14", {
            "state/example.md": (
                "state_plain", "example", b"encrypted-file",
                {"Zone.Identifier": b"encrypted-stream"},
            ),
        })
        target = foundation._config["STATE_DIR"] / "example.md"
        with (
            patch("backup.check_age_installed", return_value=True),
            patch("backup.get_private_key", return_value="synthetic-key"),
            patch("backup._supports_named_streams", return_value=False),
            patch("backup._age_decrypt_bytes", return_value=b"restored"),
            pytest.raises(SystemExit) as error,
        ):
            backup.do_install(str(archive_path), confirm=True)

        assert error.value.code == 1
        assert not target.exists()
        assert not (foundation._config["BACKUP_DIR"] / "pre-restore").exists()

    def test_v3_restore_preserves_file_and_named_stream_bytes(
        self, mock_backup_env, tmp_path,
    ):
        archive_path = _create_v3_zip(tmp_path / "source", "2026-03-14", {
            "state/example.bin": (
                "state_plain", "example", b"encrypted-file",
                {"Zone.Identifier": b"encrypted-stream"},
            ),
        })
        target = foundation._config["STATE_DIR"] / "example.bin"
        stream_writes = []
        with (
            patch("backup.check_age_installed", return_value=True),
            patch("backup.get_private_key", return_value="synthetic-key"),
            patch("backup._supports_named_streams", return_value=True),
            patch("backup._age_decrypt_bytes", side_effect=[b"\x00file\xff", b"\x00stream\xfe"]),
            patch("backup._write_named_stream",
                  side_effect=lambda path, name, data: stream_writes.append((path, name, data))),
        ):
            backup.do_install(str(archive_path), confirm=True)

        assert target.read_bytes() == b"\x00file\xff"
        assert [(name, data) for _, name, data in stream_writes] == [
            ("Zone.Identifier", b"\x00stream\xfe"),
        ]

    def test_v3_restore_does_not_follow_existing_staging_symlink(
        self, mock_backup_env, tmp_path,
    ):
        archive_path = _create_v3_zip(tmp_path / "source", "2026-03-14", {
            "state/example.bin": ("state_plain", "example", b"encrypted-file", {}),
        })
        target = foundation._config["STATE_DIR"] / "example.bin"
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"untouched")
        try:
            Path(str(target) + ".tmp").symlink_to(outside)
        except OSError:
            pytest.skip("file symlinks are unavailable")

        with (
            patch("backup.check_age_installed", return_value=True),
            patch("backup.get_private_key", return_value="synthetic-key"),
            patch("backup._age_decrypt_bytes", return_value=b"restored"),
        ):
            backup.do_install(str(archive_path), confirm=True)

        assert target.read_bytes() == b"restored"
        assert outside.read_bytes() == b"untouched"

    def test_v3_restore_prevalidates_every_payload_before_writing(
        self, mock_backup_env, tmp_path,
    ):
        archive_path = _create_v3_zip(tmp_path / "source", "2026-03-14", {
            "state/first.md": ("state_plain", "first", b"encrypted-first", {}),
            "state/second.md": ("state_plain", "second", b"encrypted-second", {}),
        })
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        manifest["files"]["state/second.md"]["data"]["sha256"] = "0" * 64
        with zipfile.ZipFile(archive_path, "w") as archive:
            for record in manifest["files"].values():
                archive.writestr(record["data"]["entry"], b"payload")
            archive.writestr("manifest.json", json.dumps(manifest))

        with (
            patch("backup.check_age_installed", return_value=True),
            patch("backup.get_private_key", return_value="synthetic-key"),
            patch("backup._age_decrypt_bytes", return_value=b"---\n# Restored\n"),
            pytest.raises(SystemExit),
        ):
            backup.do_install(str(archive_path), confirm=True)

        assert not (foundation._config["STATE_DIR"] / "first.md").exists()
        assert not (foundation._config["STATE_DIR"] / "second.md").exists()

    def test_snapshot_catalog_failure_preserves_previous_generation(self, mock_backup_env):
        state_dir = foundation._config["STATE_DIR"]
        source = state_dir / "finance.md.age"
        old_bytes = _fake_age_bytes("previous-generation")
        source.write_bytes(old_bytes)
        registry = [{
            "name": "finance",
            "source_type": "state_encrypted",
            "source_path": source,
            "restore_path": "state/finance.md.age",
        }]
        archive_path = foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        previous_zip = _create_test_zip(
            foundation._config["BACKUP_DIR"], "daily", "2026-03-09",
            {"state/finance.md.age": (
                old_bytes, "state_encrypted", "state/finance.md.age", "finance",
            )},
        )
        original_zip = previous_zip.read_bytes()
        original_catalog = backup._load_manifest()
        original_catalog["snapshots"]["daily/2026-03-09.zip"] = {
            "date": "2026-03-09", "tier": "daily",
            "sha256": backup._file_sha256(previous_zip),
            "size": previous_zip.stat().st_size, "file_count": 1,
        }
        backup._save_manifest(original_catalog)
        original_catalog_bytes = foundation._config["BACKUP_MANIFEST"].read_bytes()

        with patch("backup._save_manifest_unlocked",
                   side_effect=RuntimeError("synthetic catalog publish failure")):
            assert backup.backup_snapshot(registry, today=date(2026, 3, 9)) == 0

        assert archive_path.read_bytes() == original_zip
        assert foundation._config["BACKUP_MANIFEST"].read_bytes() == original_catalog_bytes

    def test_snapshot_rejects_missing_source_and_invalid_registry(self, mock_backup_env):
        state_dir = foundation._config["STATE_DIR"]
        missing = [{
            "name": "missing", "source_type": "state_encrypted",
            "source_path": state_dir / "missing.md.age",
            "restore_path": "state/missing.md.age",
        }]
        unsafe = [{**missing[0], "restore_path": "../missing.md.age"}]
        duplicate = [missing[0], {**missing[0], "name": "other"}]

        assert backup.backup_snapshot(missing, today=date(2026, 3, 9)) == 0
        assert backup.backup_snapshot(unsafe, today=date(2026, 3, 9)) == 0
        assert backup.backup_snapshot(duplicate, today=date(2026, 3, 9)) == 0

    def test_snapshot_aborts_on_encryption_or_stream_capture_failure(self, mock_backup_env):
        state_dir = foundation._config["STATE_DIR"]
        source = state_dir / "public.md"
        source.write_text("---\nkind: synthetic\n---\n" + "word " * 40, encoding="utf-8")
        plain_registry = [{
            "name": "public", "source_type": "state_plain", "source_path": source,
            "restore_path": "state/public.md",
        }]
        with patch("backup.get_public_key", return_value="age1synthetic"), \
             patch("backup.age_encrypt", return_value=False):
            assert backup.backup_snapshot(plain_registry, today=date(2026, 3, 9)) == 0

        encrypted = state_dir / "private.md.age"
        encrypted.write_bytes(_fake_age_bytes())
        encrypted_registry = [{
            "name": "private", "source_type": "state_encrypted", "source_path": encrypted,
            "restore_path": "state/private.md.age",
        }]
        with patch("backup._read_named_streams", side_effect=OSError("synthetic stream denial")):
            assert backup.backup_snapshot(encrypted_registry, today=date(2026, 3, 10)) == 0
        with patch("backup._read_named_streams", return_value={"bad/name": b"stream"}):
            assert backup.backup_snapshot(encrypted_registry, today=date(2026, 3, 11)) == 0
        with (
            patch("backup._read_named_streams", return_value={"Zone.Identifier": b"stream"}),
            patch("backup.get_public_key", return_value=""),
        ):
            assert backup.backup_snapshot(encrypted_registry, today=date(2026, 3, 12)) == 0

    def test_snapshot_aborts_on_database_and_archive_creation_failures(self, mock_backup_env):
        source = foundation._config["ARTHA_LOCAL_DIR"] / "state.db"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"synthetic database")
        registry = [{
            "name": "localdb__state", "source_type": "local_db", "source_path": source,
            "restore_path": ".artha-local/state.db",
        }]
        with (
            patch("backup.get_public_key", return_value="age1synthetic"),
            patch("backup._sqlite_online_snapshot", side_effect=sqlite3.DatabaseError("synthetic failure")),
        ):
            assert backup.backup_snapshot(registry, today=date(2026, 3, 9)) == 0
        assert backup.backup_snapshot([], today=date(2026, 3, 9)) == 0

        encrypted = foundation._config["STATE_DIR"] / "finance.md.age"
        encrypted.write_bytes(_fake_age_bytes())
        registry = [{
            "name": "finance", "source_type": "state_encrypted", "source_path": encrypted,
            "restore_path": "state/finance.md.age",
        }]
        with patch("backup.zipfile.ZipFile", side_effect=OSError("synthetic archive failure")):
            assert backup.backup_snapshot(registry, today=date(2026, 3, 10)) == 0

    def test_snapshot_prune_failure_is_nonfatal(self, mock_backup_env):
        source = foundation._config["STATE_DIR"] / "finance.md.age"
        source.write_bytes(_fake_age_bytes())
        registry = [{
            "name": "finance", "source_type": "state_encrypted", "source_path": source,
            "restore_path": "state/finance.md.age",
        }]
        with (
            patch("backup._prune_backups", side_effect=RuntimeError("synthetic prune failure")),
            patch("backup.do_validate_backup"),
        ):
            assert backup.backup_snapshot(registry, today=date(2026, 3, 9)) == 1

    def test_snapshot_closes_knowledge_graph_after_backup(self, mock_backup_env):
        source = foundation._config["STATE_DIR"] / "finance.md.age"
        source.write_bytes(_fake_age_bytes())
        registry = [{
            "name": "finance", "source_type": "state_encrypted", "source_path": source,
            "restore_path": "state/finance.md.age",
        }]
        knowledge_graph = MagicMock()
        knowledge_graph.backup.side_effect = [None, RuntimeError("synthetic KB backup failure")]
        with (
            patch("lib.knowledge_graph.KnowledgeGraph", return_value=knowledge_graph),
            patch("backup._read_named_streams", return_value={}),
            patch("backup._prune_backups"),
            patch("backup.do_validate_backup"),
        ):
            assert backup.backup_snapshot(registry, today=date(2026, 3, 9)) == 1
            assert backup.backup_snapshot(registry, today=date(2026, 3, 10)) == 1
        assert knowledge_graph.backup.call_count == 2
        assert knowledge_graph.close.call_count == 2

    def test_snapshot_catalog_failure_removes_new_archive(self, mock_backup_env):
        source = foundation._config["STATE_DIR"] / "finance.md.age"
        source.write_bytes(_fake_age_bytes())
        registry = [{
            "name": "finance", "source_type": "state_encrypted", "source_path": source,
            "restore_path": "state/finance.md.age",
        }]
        archive_path = foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip"
        with patch("backup._save_manifest_unlocked", side_effect=RuntimeError("synthetic failure")):
            assert backup.backup_snapshot(registry, today=date(2026, 3, 9)) == 0
        assert not archive_path.exists()

    def test_restore_checksum_mismatch_fails_before_decryption(self, mock_backup_env, tmp_path):
        archive_path = _create_v3_zip(tmp_path / "source", "2026-03-14", {
            "state/example.bin": ("state_plain", "example", b"encrypted-file", {}),
        })
        with (
            patch("backup._age_decrypt_bytes") as decrypt,
            pytest.raises(SystemExit),
        ):
            backup._restore_from_zip(
                archive_path, None, False, confirm=True,
                expected_catalog={"sha256": "0" * 64},
            )
        decrypt.assert_not_called()
        assert not (foundation._config["STATE_DIR"] / "example.bin").exists()

    def test_restore_rejects_missing_catalog_file_and_domain(self, mock_backup_env, tmp_path):
        archive_path = _create_v3_zip(tmp_path / "source", "2026-03-14", {
            "state/example.bin": ("state_plain", "example", b"encrypted-file", {}),
        })
        with patch("backup._file_sha256", side_effect=OSError("synthetic read failure")), \
             pytest.raises(SystemExit):
            backup._restore_from_zip(
                archive_path, None, False, confirm=True,
                expected_catalog={"sha256": "0" * 64},
            )
        with pytest.raises(SystemExit):
            backup._restore_from_zip(archive_path, "missing", False)

    def test_restore_preview_shows_streams_and_execution_requires_age(
        self, mock_backup_env, tmp_path, capsys,
    ):
        archive_path = _create_v3_zip(tmp_path / "source", "2026-03-14", {
            "state/example.bin": (
                "state_plain", "example", b"encrypted-file",
                {"Zone.Identifier": b"encrypted-stream"},
            ),
        })
        with patch("backup._supports_named_streams", return_value=True):
            backup._restore_from_zip(archive_path, None, True, confirm=False)
        assert "Zone.Identifier" in capsys.readouterr().out
        with (
            patch("backup._supports_named_streams", return_value=True),
            patch("backup.check_age_installed", return_value=False),
            pytest.raises(SystemExit),
        ):
            backup._restore_from_zip(archive_path, None, False, confirm=True)

    def test_restore_write_failure_is_reported_and_staging_is_removed(
        self, mock_backup_env, tmp_path,
    ):
        archive_path = _create_v3_zip(tmp_path / "source", "2026-03-14", {
            "state/example.bin": ("state_plain", "example", b"encrypted-file", {}),
        })
        with (
            patch("backup.check_age_installed", return_value=True),
            patch("backup.get_private_key", return_value="synthetic-key"),
            patch("backup._age_decrypt_bytes", return_value=b"restored"),
            patch("backup.os.replace", side_effect=PermissionError("synthetic write denial")),
            pytest.raises(SystemExit) as error,
        ):
            backup.do_install(str(archive_path), confirm=True)

        assert error.value.code == 1
        state_dir = foundation._config["STATE_DIR"]
        assert not (state_dir / "example.bin").exists()
        assert not list(state_dir.glob(".artha-restore-*.tmp"))


class TestArchiveSafetyHelpers:
    def test_safe_paths_and_stream_names_reject_ambiguous_values(self):
        assert backup._safe_relative_path("state/example.md")
        assert backup._safe_stream_name("Zone.Identifier")
        for value in (None, "", "/absolute", "../escape", "state//file", "state\\file", "C:/file"):
            assert not backup._safe_relative_path(value)
        for value in ("", ".", "..", "stream:name", "stream/name", "stream\\name", "bad*name"):
            assert not backup._safe_stream_name(value)

    def test_destination_rejects_escape_and_linked_ancestor(self, tmp_path):
        with pytest.raises(ValueError, match="unsafe restore path"):
            backup._safe_destination(tmp_path, "../escape")
        linked = tmp_path / "linked"
        target = tmp_path / "target"
        target.mkdir()
        try:
            linked.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlinks are unavailable")
        with pytest.raises(ValueError, match="linked restore destination"):
            backup._safe_destination(tmp_path, "linked/file")

    def test_payload_descriptor_and_duplicate_json_keys_are_rejected(self):
        valid = {
            "entry": "payload/1.age",
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "size": 1,
        }
        assert backup._descriptor(valid, version="3") == valid
        for broken in (
            {**valid, "entry": "payload/../file.age"},
            {**valid, "sha256": "not-a-digest"},
            {**valid, "size": True},
            {**valid, "size": -1},
        ):
            with pytest.raises(ValueError):
                backup._descriptor(broken, version="3")
        with pytest.raises(ValueError, match="duplicate JSON key"):
            backup._unique_json_pairs([("version", "2"), ("version", "3")])

    @pytest.mark.parametrize(
        "defect",
        [
            "invalid_date", "invalid_source_type", "unsafe_restore_path",
            "restore_path_mismatch", "missing_streams", "unsafe_stream_name",
            "bad_payload_size", "duplicate_payload_entry", "catalog_file_count",
            "catalog_stream_count", "empty_manifest",
        ],
    )
    def test_archive_records_reject_invalid_manifest_and_catalog(self, mock_backup_env, defect):
        archive_path = _create_v3_zip(foundation._config["BACKUP_DIR"], "2026-03-13", {
            "state/example.bin": (
                "state_plain", "example", b"encrypted-file",
                {"Zone.Identifier": b"encrypted-stream"},
            ),
        })
        catalog = backup._load_manifest()["snapshots"]["daily/2026-03-13.zip"]
        with zipfile.ZipFile(archive_path) as archive:
            internal = json.loads(archive.read("manifest.json"))
            file_record = internal["files"]["state/example.bin"]
            if defect == "invalid_date":
                internal["date"] = "not-a-date"
            elif defect == "invalid_source_type":
                file_record["source_type"] = "unknown"
            elif defect == "unsafe_restore_path":
                file_record["restore_path"] = "../outside"
            elif defect == "restore_path_mismatch":
                file_record["restore_path"] = "state/other.bin"
            elif defect == "missing_streams":
                file_record["streams"] = None
            elif defect == "unsafe_stream_name":
                file_record["streams"]["bad/name"] = file_record["streams"].pop("Zone.Identifier")
            elif defect == "bad_payload_size":
                file_record["data"]["size"] = True
            elif defect == "duplicate_payload_entry":
                file_record["streams"]["Zone.Identifier"]["entry"] = file_record["data"]["entry"]
            elif defect == "catalog_file_count":
                catalog = {**catalog, "file_count": 2}
            elif defect == "catalog_stream_count":
                catalog = {**catalog, "stream_count": 0}
            else:
                internal["files"] = {}

            with pytest.raises(ValueError):
                backup._archive_records(archive, internal, catalog)

    def test_archive_records_reject_directory_entries(self, mock_backup_env):
        archive_path = _create_v3_zip(foundation._config["BACKUP_DIR"], "2026-03-13", {
            "state/example.bin": ("state_plain", "example", b"encrypted-file", {}),
        })
        with zipfile.ZipFile(archive_path, "a") as archive:
            archive.writestr("payload/", b"")
        with zipfile.ZipFile(archive_path) as archive:
            internal = json.loads(archive.read("manifest.json"))
            with pytest.raises(ValueError, match="directory entry"):
                backup._archive_records(archive, internal)

    def test_archive_records_reject_source_and_catalog_mismatches(self, mock_backup_env):
        archive_path = _create_v3_zip(foundation._config["BACKUP_DIR"], "2026-03-13", {
            "state/example.bin": ("state_plain", "example", b"encrypted-file", {}),
        })
        with zipfile.ZipFile(archive_path) as archive:
            internal = json.loads(archive.read("manifest.json"))
            internal["files"]["state/example.bin"]["restore_path"] = "config/example.bin"
            with pytest.raises(ValueError, match="does not match source type"):
                backup._archive_records(archive, internal)

        with zipfile.ZipFile(archive_path) as archive:
            internal = json.loads(archive.read("manifest.json"))
            catalog = backup._load_manifest()["snapshots"]["daily/2026-03-13.zip"]
            catalog["date"] = "2026-03-12"
            with pytest.raises(ValueError, match="catalog metadata"):
                backup._archive_records(archive, internal, catalog)

    def test_version_two_descriptor_and_payload_path_are_rejected(self, mock_backup_env):
        with pytest.raises(ValueError, match="unsafe version-2"):
            backup._descriptor({
                "entry": "../outside.age",
                "sha256": hashlib.sha256(b"x").hexdigest(),
                "size": 1,
            }, version="2")

        archive_path = _create_test_zip(
            foundation._config["BACKUP_DIR"], "daily", "2026-03-12",
            {"state/wrong.md.age": (
                _fake_age_bytes(), "state_encrypted", "state/example.md.age", "example",
            )},
        )
        with zipfile.ZipFile(archive_path) as archive:
            internal = json.loads(archive.read("manifest.json"))
            with pytest.raises(ValueError, match="version-2 payload path"):
                backup._archive_records(archive, internal)

    def test_content_validation_covers_database_markdown_and_missing_yaml(self, monkeypatch):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE sample (value TEXT)")
        image = connection.serialize()
        connection.close()
        backup._validate_content("local_db", ".artha-local/sample.db", image)
        with pytest.raises(ValueError, match="not UTF-8"):
            backup._validate_content("state_plain", "state/example.md", b"\xff")
        monkeypatch.setattr(backup, "_yaml", None)
        with pytest.raises(ValueError, match="unavailable"):
            backup._validate_content("config", "config/example.yaml", b"enabled: true\n")

    def test_source_type_validation_handles_structured_and_binary_payloads(self):
        backup._validate_content("config", "config/settings.yaml", b"enabled: true\n")
        backup._validate_content("config", "config/settings.json", b'{"enabled": true}')
        backup._validate_content("config", "config/blob.bin", b"\x00\xff")
        backup._validate_content("state_plain", "state/blob.bin", b"\x00\xff")
        with pytest.raises(ValueError, match="invalid JSON"):
            backup._validate_content("config", "config/settings.json", b"{")
        with pytest.raises(ValueError, match="invalid YAML"):
            backup._validate_content("config", "config/settings.yaml", b"\xff")
        with pytest.raises(ValueError, match="empty payload"):
            backup._validate_content("config", "config/blob.bin", b"")
        with pytest.raises(ValueError, match="frontmatter"):
            backup._validate_content("state_plain", "state/example.md", b"# Missing frontmatter")

    def test_sqlite_snapshot_rejects_missing_source_and_invalid_image(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            backup._sqlite_online_snapshot(tmp_path / "missing.db")
        with pytest.raises(ValueError, match="invalid SQLite image"):
            backup._validate_sqlite_image(b"not a database")

    def test_read_named_streams_enumerates_and_reads_stream(self, monkeypatch, tmp_path):
        source = tmp_path / "source.bin"
        source.write_bytes(b"file")
        calls = [0]

        class FakeFunction:
            def __init__(self, function):
                self.function = function

            def __call__(self, *args):
                return self.function(*args)

        class FakeKernel:
            pass

        def find_first(_path, _level, data, _size):
            data._obj.name = "::$DATA"
            data._obj.size = 4
            return 1

        def find_next(_handle, data):
            calls[0] += 1
            if calls[0] == 1:
                data._obj.name = ":Zone.Identifier:$DATA"
                data._obj.size = 5
                return 1
            return 0

        kernel = FakeKernel()
        kernel.FindFirstStreamW = FakeFunction(find_first)
        kernel.FindNextStreamW = FakeFunction(find_next)
        kernel.FindClose = FakeFunction(lambda _handle: 1)
        monkeypatch.setattr(backup, "_supports_named_streams", lambda: True)
        monkeypatch.setattr(backup.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel, raising=False)
        monkeypatch.setattr(backup.ctypes, "get_last_error", lambda: 38, raising=False)
        with patch("builtins.open", return_value=io.BytesIO(b"label")) as open_stream:
            assert backup._read_named_streams(source) == {"Zone.Identifier": b"label"}
        open_stream.assert_called_once_with(f"{source}:Zone.Identifier", "rb")

    def test_read_named_streams_reports_enumeration_errors(self, monkeypatch, tmp_path):
        source = tmp_path / "source.bin"
        source.write_bytes(b"file")
        monkeypatch.setattr(backup, "_supports_named_streams", lambda: True)

        def fake_kernel(name, handle, error):
            kernel = MagicMock()

            def find_first(_path, _level, data, _size):
                data._obj.name = name
                return handle

            kernel.FindFirstStreamW.side_effect = find_first
            kernel.FindNextStreamW.return_value = 0
            monkeypatch.setattr(
                backup.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel, raising=False,
            )
            monkeypatch.setattr(
                backup.ctypes, "get_last_error", lambda: error, raising=False,
            )
            return kernel

        invalid_handle = backup.ctypes.c_void_p(-1).value
        fake_kernel("::$DATA", invalid_handle, 38)
        assert backup._read_named_streams(source) == {}
        fake_kernel("::$DATA", invalid_handle, 5)
        with pytest.raises(OSError, match="enumerate NTFS streams"):
            backup._read_named_streams(source)

        kernel = fake_kernel(":bad/name:$DATA", 1, 0)
        with pytest.raises(ValueError, match="unsupported NTFS stream"):
            backup._read_named_streams(source)
        kernel.FindClose.assert_called_once_with(1)

        fake_kernel("::$DATA", 1, 0)
        monkeypatch.setattr(backup.ctypes, "get_last_error", lambda: 5, raising=False)
        with pytest.raises(OSError, match="enumerate NTFS streams"):
            backup._read_named_streams(source)

    def test_write_named_stream_uses_native_stream_path(self, monkeypatch):
        monkeypatch.setattr(backup, "_supports_named_streams", lambda: True)
        stream = MagicMock()
        stream.__enter__.return_value = stream
        with patch("builtins.open", return_value=stream) as open_stream:
            backup._write_named_stream(Path("restored.bin"), "Zone.Identifier", b"label")
        open_stream.assert_called_once_with("restored.bin:Zone.Identifier", "wb")
        stream.write.assert_called_once_with(b"label")

    def test_write_named_stream_rejects_unsafe_name_before_open(self, monkeypatch):
        monkeypatch.setattr(backup, "_supports_named_streams", lambda: True)
        with patch("builtins.open") as open_stream, pytest.raises(ValueError, match="unsafe NTFS stream"):
            backup._write_named_stream(Path("restored.bin"), "bad:name", b"label")
        open_stream.assert_not_called()

    def test_age_byte_helpers_use_memory_for_plaintext_and_stage_ciphertext_only(self, tmp_path):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"encrypted")
        with patch("backup._find_age_bin", return_value="age.exe"), \
             patch("backup.subprocess.run", return_value=completed) as run:
            assert backup._age_encrypt_bytes("age1synthetic", b"plain") == b"encrypted"
            args, kwargs = run.call_args
            assert args[0] == ["age.exe", "--recipient", "age1synthetic"]
            assert kwargs["input"] == b"plain"

        seen = {}

        def create_ciphertext_stage(**_kwargs):
            staged = tmp_path / "ciphertext.age"
            return os.open(staged, os.O_CREAT | os.O_RDWR), str(staged)

        def decrypt_process(args, **kwargs):
            staged = Path(args[-1])
            seen["staged"] = staged
            seen["ciphertext"] = staged.read_bytes()
            seen["identity"] = kwargs["input"]
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"plain")

        with patch("backup._find_age_bin", return_value="age.exe"), \
             patch("backup.tempfile.mkstemp", side_effect=create_ciphertext_stage), \
             patch("backup.subprocess.run", side_effect=decrypt_process):
            assert backup._age_decrypt_bytes("AGE-IDENTITY", b"ciphertext") == b"plain"
        assert seen["ciphertext"] == b"ciphertext"
        assert seen["identity"] == b"AGE-IDENTITY\n"
        assert not seen["staged"].exists()

    def test_age_byte_helpers_report_missing_or_failed_age_process(self):
        with patch("backup._find_age_bin", return_value=None), pytest.raises(RuntimeError, match="not found"):
            backup._age_encrypt_bytes("age1synthetic", b"plain")
        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"")
        with patch("backup._find_age_bin", return_value="age.exe"), \
             patch("backup.subprocess.run", return_value=failed), \
             pytest.raises(RuntimeError, match="encryption failed"):
            backup._age_encrypt_bytes("age1synthetic", b"plain")

    def test_age_decrypt_failure_removes_ciphertext_stage(self, tmp_path):
        staged = tmp_path / "ciphertext.age"

        def create_ciphertext_stage(**_kwargs):
            return os.open(staged, os.O_CREAT | os.O_RDWR), str(staged)

        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"")
        with patch("backup._find_age_bin", return_value="age.exe"), \
             patch("backup.tempfile.mkstemp", side_effect=create_ciphertext_stage), \
             patch("backup.subprocess.run", return_value=failed), \
             pytest.raises(RuntimeError, match="decryption failed"):
            backup._age_decrypt_bytes("AGE-IDENTITY", b"ciphertext")
        assert not staged.exists()

    def test_age_decrypt_fails_when_age_is_unavailable(self):
        with patch("backup._find_age_bin", return_value=None), \
             pytest.raises(RuntimeError, match="age CLI not found"):
            backup._age_decrypt_bytes("synthetic-key", b"ciphertext")

    def test_publication_lock_serializes_catalog_writes(self):
        with backup._publication_lock():
            assert (foundation._config["BACKUP_DIR"] / ".publication.lock").exists()

    def test_unsupported_host_refuses_named_stream_io(self, tmp_path):
        source = tmp_path / "source.bin"
        source.write_bytes(b"synthetic")
        with patch("backup._supports_named_streams", return_value=False):
            assert backup._read_named_streams(source) == {}
            with pytest.raises(OSError, match="unavailable"):
                backup._write_named_stream(source, "Zone.Identifier", b"label")
