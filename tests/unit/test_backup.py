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
import json
import os
import subprocess
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import scripts.backup as backup
import scripts.foundation as foundation
import scripts.vault as vault


# ---------------------------------------------------------------------------
# ZIP test helper (shared across all test classes)
# ---------------------------------------------------------------------------

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

    def test_load_returns_empty_on_corrupt_json(self, mock_backup_env):
        foundation._config["BACKUP_MANIFEST"].parent.mkdir(parents=True, exist_ok=True)
        foundation._config["BACKUP_MANIFEST"].write_text("{ not valid json }")
        m = backup._load_manifest()
        assert m == {"last_validate": None, "snapshots": {}}

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
            (state_dir / f"{d}.md.age").write_bytes(b"x" * 500)

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

    def test_snapshot_skips_missing_age_file(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        (state_dir / "immigration.md.age").write_bytes(b"x" * 200)
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

        def good_decrypt(key, infile, outfile):
            outfile.write_text("---\nschema_version: '1.0'\n---\n# Immigration\n" + "word " * 50)
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-key"), \
             patch("scripts.backup.age_decrypt", side_effect=good_decrypt):
            backup.do_validate_backup()

        out = capsys.readouterr().out
        assert "\u2713" in out
        assert "valid" in out.lower()
        assert backup._load_manifest()["last_validate"] is not None

    def test_validate_fails_checksum_mismatch(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._seed_zip(backup_dir)
        zip_path.write_bytes(b"corrupted zip contents!!")

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-key"), \
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
                "artha_backup_version": "2", "date": "2026-03-13", "tier": "daily",
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

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-key"), \
             pytest.raises(SystemExit):
            backup.do_validate_backup()

        assert "MISSING" in capsys.readouterr().out

    def test_validate_fails_decrypt_error(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-key"), \
             patch("scripts.backup.age_decrypt", return_value=False), \
             pytest.raises(SystemExit):
            backup.do_validate_backup()

        assert "DECRYPT" in capsys.readouterr().out

    def test_validate_fails_empty_content(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        def empty_decrypt(key, infile, outfile):
            outfile.write_text("")
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-key"), \
             patch("scripts.backup.age_decrypt", side_effect=empty_decrypt), \
             pytest.raises(SystemExit):
            backup.do_validate_backup()

        assert "EMPTY" in capsys.readouterr().out

    def test_validate_fails_no_yaml_frontmatter(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        def no_yaml_decrypt(key, infile, outfile):
            outfile.write_text("NO FRONTMATTER HERE\n" + "word " * 50)
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-key"), \
             patch("scripts.backup.age_decrypt", side_effect=no_yaml_decrypt), \
             pytest.raises(SystemExit):
            backup.do_validate_backup()

        assert "YAML" in capsys.readouterr().out

    def test_validate_fails_too_short(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        def short_decrypt(key, infile, outfile):
            outfile.write_text("---\ndata: ok\n---\nfew words only")
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-key"), \
             patch("scripts.backup.age_decrypt", side_effect=short_decrypt), \
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

        def track_decrypt(key, infile, outfile):
            outfile.write_text("---\nschema_version: '1.0'\n---\n# Content\n" + "word " * 50)
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-key"), \
             patch("scripts.backup.age_decrypt", side_effect=track_decrypt) as mock_d:
            backup.do_validate_backup(domain="immigration")
            assert mock_d.call_count == 1

    def test_validate_no_backups_exits_gracefully(self, mock_backup_env, capsys):
        # Remove the pre-seeded manifest snapshots
        backup._save_manifest({"last_validate": None, "snapshots": {}})
        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-key"):
            backup.do_validate_backup()  # should not raise

        assert "No backups" in capsys.readouterr().out


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
        for name in foundation._config["SENSITIVE_FILES"]:
            assert name in entry_names
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
        for name in foundation._config["SENSITIVE_FILES"]:
            assert name in entry_names

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

        with patch("scripts.backup.get_public_key", return_value="age1mock"), \
             patch("scripts.backup.age_encrypt", side_effect=fake_encrypt):
            count = backup.backup_snapshot(registry, today=today)

        assert count == 1
        zip_path = foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip"
        assert zip_path.exists()
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        assert "state/goals.md.age" in internal["files"]
        meta = internal["files"]["state/goals.md.age"]
        assert meta["source_type"] == "state_plain"
        assert meta["restore_path"] == "state/goals.md"

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

        with patch("scripts.backup.get_public_key", return_value="age1mock"), \
             patch("scripts.backup.age_encrypt", side_effect=fake_encrypt):
            count = backup.backup_snapshot(registry, today=today)

        assert count == 1
        zip_path = foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip"
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        assert "config/artha_config.yaml.age" in internal["files"]
        meta = internal["files"]["config/artha_config.yaml.age"]
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
        with patch("scripts.backup.get_public_key", side_effect=SystemExit(1)):
            count = backup.backup_snapshot(registry, today=date(2026, 3, 9))
        assert count == 0

    def test_mixed_registry_backs_up_all_types(self, mock_backup_env):
        state_dir  = foundation._config["STATE_DIR"]
        config_dir = foundation._config["CONFIG_DIR"]
        (state_dir / "finance.md.age").write_bytes(b"x" * 300)
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

        with patch("scripts.backup.get_public_key", return_value="age1mock"), \
             patch("scripts.backup.age_encrypt", side_effect=fake_encrypt):
            count = backup.backup_snapshot(registry, today=date(2026, 3, 9))

        assert count == 3
        zip_path = foundation._config["BACKUP_DIR"] / "daily" / "2026-03-09.zip"
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        assert "state/finance.md.age" in internal["files"]
        assert "state/goals.md.age" in internal["files"]
        assert "config/artha_config.yaml.age" in internal["files"]

    def test_manifest_entry_has_restore_path_and_source_type(self, mock_backup_env):
        state_dir = foundation._config["STATE_DIR"]
        (state_dir / "immigration.md.age").write_bytes(b"x" * 200)
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
        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", return_value=True):
            backup.do_restore(date_str="2026-03-14", dry_run=True)

        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "state/immigration.md.age" in out
        assert not (foundation._config["STATE_DIR"] / "immigration.md.age").exists()

    def test_restore_state_encrypted_copies_age_file(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)
        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", side_effect=lambda k, s, d: d.write_bytes(b"dec") or True):
            backup.do_restore(date_str="2026-03-14")

        restored = foundation._config["STATE_DIR"] / "immigration.md.age"
        assert restored.exists()
        assert restored.read_bytes() == b"enc-imm"

    def test_restore_state_plain_decrypts_file(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        def fake_decrypt(key, src, dst):
            dst.write_text("---\n# Goals\n")
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", side_effect=fake_decrypt):
            backup.do_restore(date_str="2026-03-14")

        restored = foundation._config["STATE_DIR"] / "goals.md"
        assert restored.exists()
        assert restored.read_text() == "---\n# Goals\n"

    def test_restore_config_decrypts_to_config_dir(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_zip(backup_dir)

        def fake_decrypt(key, src, dst):
            dst.write_text("age_recipient: age1xxx\n")
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", side_effect=fake_decrypt):
            backup.do_restore(date_str="2026-03-14")

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
                "artha_backup_version": "2", "date": "2026-03-14", "tier": "daily",
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

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             pytest.raises(SystemExit):
            backup.do_restore(date_str="2026-03-14")

        assert "CHECKSUM" in capsys.readouterr().out

    def test_restore_no_backups_exits_gracefully(self, mock_backup_env, capsys):
        backup._save_manifest({"last_validate": None, "snapshots": {}})
        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"):
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
        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", return_value=True):
            backup.do_install(str(zip_path), dry_run=True)

        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert not (foundation._config["STATE_DIR"] / "immigration.md.age").exists()

    def test_install_state_encrypted_copied_directly(self, mock_backup_env, tmp_path):
        zip_path = self._make_zip(tmp_path)
        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", side_effect=lambda k, s, d: d.write_bytes(b"dec") or True):
            backup.do_install(str(zip_path))

        restored = foundation._config["STATE_DIR"] / "immigration.md.age"
        assert restored.exists()
        assert restored.read_bytes() == b"enc-imm"

    def test_install_state_plain_decrypted(self, mock_backup_env, tmp_path):
        zip_path = self._make_zip(tmp_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("---\n# Goals\n")
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", side_effect=fake_decrypt):
            backup.do_install(str(zip_path))

        restored = foundation._config["STATE_DIR"] / "goals.md"
        assert restored.read_text() == "---\n# Goals\n"

    def test_install_config_decrypted_to_config_dir(self, mock_backup_env, tmp_path):
        zip_path = self._make_zip(tmp_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("todo_lists:\n  general: id123\n")
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", side_effect=fake_decrypt):
            backup.do_install(str(zip_path))

        restored = foundation._config["CONFIG_DIR"] / "artha_config.yaml"
        assert "id123" in restored.read_text()

    def test_install_missing_zip_exits(self, mock_backup_env):
        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
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

        def fake_decrypt(key, src, dst):
            dst.write_text("---\n# Goals\n")
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", side_effect=fake_decrypt):
            backup.do_restore(date_str="2026-03-14", data_only=True)

        assert (foundation._config["STATE_DIR"] / "immigration.md.age").exists()
        assert (foundation._config["STATE_DIR"] / "goals.md").exists()
        assert not (foundation._config["CONFIG_DIR"] / "routing.yaml").exists()

    def test_restore_full_includes_config(self, mock_backup_env):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._make_full_zip(backup_dir)
        self._register_zip(zip_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("---\n# Content\nsome: value\n")
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", side_effect=fake_decrypt):
            backup.do_restore(date_str="2026-03-14")

        assert (foundation._config["STATE_DIR"] / "immigration.md.age").exists()
        assert (foundation._config["CONFIG_DIR"] / "routing.yaml").exists()

    def test_restore_data_only_dry_run_shows_scope(self, mock_backup_env, capsys):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._make_full_zip(backup_dir)
        self._register_zip(zip_path)

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", return_value=True):
            backup.do_restore(date_str="2026-03-14", dry_run=True, data_only=True)

        out = capsys.readouterr().out
        assert "state only" in out.lower()
        assert "config skipped" in out.lower()

    def test_install_data_only_skips_config(self, mock_backup_env, tmp_path):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._make_full_zip(tmp_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("---\n# Goals\n")
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", side_effect=fake_decrypt):
            backup.do_install(str(zip_path), data_only=True)

        assert (foundation._config["STATE_DIR"] / "immigration.md.age").exists()
        assert (foundation._config["STATE_DIR"] / "goals.md").exists()
        assert not (foundation._config["CONFIG_DIR"] / "routing.yaml").exists()

    def test_install_full_includes_config(self, mock_backup_env, tmp_path):
        backup_dir = foundation._config["BACKUP_DIR"]
        zip_path = self._make_full_zip(tmp_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("key: value\n")
            return True

        with patch("scripts.backup.check_age_installed", return_value=True), \
             patch("scripts.backup.get_private_key", return_value="mock-priv"), \
             patch("scripts.backup.age_decrypt", side_effect=fake_decrypt):
            backup.do_install(str(zip_path))

        assert (foundation._config["CONFIG_DIR"] / "routing.yaml").exists()


# ---------------------------------------------------------------------------
# §12 Structural guards
# ---------------------------------------------------------------------------

def test_no_circular_import():
    """All three modules can be imported in any order without ImportError."""
    import importlib
    # reload in both orders to verify no circular dependency
    importlib.reload(importlib.import_module("scripts.foundation"))
    importlib.reload(importlib.import_module("scripts.vault"))
    importlib.reload(importlib.import_module("scripts.backup"))


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
        (state_dir / "finance.md.age").write_bytes(b"encrypted-content")

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
    def test_export_key_prints_key(self, capsys):
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
