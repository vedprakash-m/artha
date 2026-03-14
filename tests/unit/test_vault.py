import hashlib
import json
import pytest
import os
import shutil
import subprocess
import time
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import vault logic
import scripts.vault as vault

# ---------------------------------------------------------------------------
# ZIP test helpers
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


@pytest.fixture
def mock_vault_env(temp_artha_dir, monkeypatch):
    """Set up a mock environment for vault.py."""
    backup_dir = temp_artha_dir / "backups"  # at project root per new design
    monkeypatch.setattr(vault, "ARTHA_DIR", temp_artha_dir)
    monkeypatch.setattr(vault, "STATE_DIR", temp_artha_dir / "state")
    monkeypatch.setattr(vault, "CONFIG_DIR", temp_artha_dir / "config")
    monkeypatch.setattr(vault, "LOCK_FILE", temp_artha_dir / ".artha-decrypted")
    monkeypatch.setattr(vault, "AUDIT_LOG", temp_artha_dir / "state" / "audit.md")
    monkeypatch.setattr(vault, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(vault, "BACKUP_MANIFEST", backup_dir / "manifest.json")

    # Create required files
    settings_md = temp_artha_dir / "config" / "settings.md"
    # vault.py uses age_recipient
    settings_md.write_text("age_recipient: age1mockpublickey\n")

    return temp_artha_dir

def test_vault_status_inactive(mock_vault_env, capsys):
    """Verify status report when vault is inactive (encrypted)."""
    mock_run = MagicMock(stdout="age v1.1.1", stderr="", returncode=0)
    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("subprocess.run", return_value=mock_run):
        vault.do_status()
        captured = capsys.readouterr()
        assert "SESSION: INACTIVE" in captured.out
        assert "[MISSING]   immigration" in captured.out

def test_vault_health_ok(mock_vault_env, capsys):
    """Verify health check passes when everything is set up."""
    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="age v1.1.1", returncode=0)
        
        with pytest.raises(SystemExit) as exc:
            vault.do_health()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "vault.py health: OK" in captured.out

def test_vault_decrypt_flow(mock_vault_env, capsys):
    """Verify the decryption flow (mocking age calls)."""
    # Create mock .age files
    age_file = mock_vault_env / "state" / "immigration.md.age"
    age_file.write_text("encrypted-data")
    
    # Create contacts age file (contacts lives in state/ alongside other sensitive files)
    contacts_age = mock_vault_env / "state" / "contacts.md.age"
    contacts_age.write_text("encrypted-contacts")
    
    def side_effect_decrypt(key, infile, outfile):
        outfile.write_text("---\nschema_version: 1.0\n---\n# Decrypted Data")
        return True

    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("scripts.vault.age_decrypt", side_effect=side_effect_decrypt) as mock_decrypt:
        
        vault.do_decrypt()
        
        # Verify lock file created
        assert (mock_vault_env / ".artha-decrypted").exists()
        assert (mock_vault_env / "state" / "immigration.md").exists()
        assert (mock_vault_env / "state" / "contacts.md").exists()
        assert mock_decrypt.called
        captured = capsys.readouterr()
        assert "Decrypt complete" in captured.out

def test_vault_encrypt_flow(mock_vault_env, capsys):
    """Verify the encryption flow (mocking age calls)."""
    # Create mock .md files and a lock file
    (mock_vault_env / "state" / "immigration.md").write_text("plain-data")
    (mock_vault_env / "state" / "contacts.md").write_text("plain-contacts")
    (mock_vault_env / ".artha-decrypted").touch()
    
    def side_effect_encrypt(pubkey, infile, outfile):
        outfile.write_text("encrypted-data")
        return True

    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("scripts.vault.get_public_key", return_value="age1mock"), \
         patch("scripts.vault.age_encrypt", side_effect=side_effect_encrypt) as mock_encrypt:
        
        vault.do_encrypt()
        
        # Verify lock file removed
        assert not (mock_vault_env / ".artha-decrypted").exists()
        assert not (mock_vault_env / "state" / "immigration.md").exists()
        assert (mock_vault_env / "state" / "immigration.md.age").exists()
        assert (mock_vault_env / "state" / "contacts.md.age").exists()
        assert mock_encrypt.called
        captured = capsys.readouterr()
        assert "Encrypt complete" in captured.out

def test_stale_lock_handling(mock_vault_env, capsys):
    """Verify that stale locks are detected and auto-cleared if requested."""
    lock_file = mock_vault_env / ".artha-decrypted"
    lock_file.touch()
    
    # Set mtime to 31 minutes ago
    stale_time = time.time() - (31 * 60)
    os.utime(lock_file, (stale_time, stale_time))
    
    def side_effect_decrypt(key, infile, outfile):
        outfile.write_text("---\nschema_version: 1.0\n---\n# Decrypted Data")
        return True

    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("scripts.vault.age_decrypt", side_effect=side_effect_decrypt):
        
        # do_decrypt should clear stale lock and proceed
        vault.do_decrypt()
        assert lock_file.exists() # It was removed and re-created
        captured = capsys.readouterr()
        assert "Stale lock file detected" in captured.out
        assert "Auto-clearing" in captured.out

def test_integrity_write_guard(mock_vault_env, capsys):
    """Verify that Net-Negative Write Guard blocks encryption on significant data loss."""
    plain_file = mock_vault_env / "state" / "immigration.md"
    age_file = mock_vault_env / "state" / "immigration.md.age"
    
    # Create a large "old" age file and a tiny "new" plain file
    age_file.write_text("X" * 1000)
    plain_file.write_text("tiny")
    (mock_vault_env / ".artha-decrypted").touch()
    # Remove audit.md created by fixture to prevent age_encrypt being called for it
    (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)
    
    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("scripts.vault.get_public_key", return_value="age1mock"), \
         patch("scripts.vault.age_encrypt", return_value=True):
        
        # do_encrypt should skip this file and eventually exit non-zero due to errors
        with pytest.raises(SystemExit):
            vault.do_encrypt()
        
        assert plain_file.exists() # Not deleted
        assert age_file.read_text() == "X" * 1000 # Not overwritten
        captured = capsys.readouterr()
        assert "INTEGRITY ALERT" in captured.out
        assert "Skipping encryption to prevent data loss" in captured.err


# ---------------------------------------------------------------------------
# Layer 1 backup restore path tests
# ---------------------------------------------------------------------------

def test_decrypt_empty_output_restores_backup(mock_vault_env, capsys):
    """If age_decrypt returns True but output is empty, backup is restored."""
    age_file = mock_vault_env / "state" / "immigration.md.age"
    plain_file = mock_vault_env / "state" / "immigration.md"
    bak_file = mock_vault_env / "state" / "immigration.md.bak"
    age_file.write_text("encrypted-data")
    # Prior plaintext exists (becomes the backup)
    plain_file.write_text("---\nprevious: good data\n---\n# Prior Content")

    def empty_decrypt(key, infile, outfile):
        outfile.write_text("")  # empty output — simulates corrupt decrypt
        return True

    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("scripts.vault.age_decrypt", side_effect=empty_decrypt):
        with pytest.raises(SystemExit):
            vault.do_decrypt()

    captured = capsys.readouterr()
    assert "empty" in captured.err.lower()
    # Backup should have been restored to plain_file
    assert plain_file.exists()
    assert "Prior Content" in plain_file.read_text()
    assert not bak_file.exists()


def test_decrypt_invalid_yaml_restores_backup(mock_vault_env, capsys):
    """If age_decrypt produces a file without YAML frontmatter, backup is restored."""
    age_file = mock_vault_env / "state" / "immigration.md.age"
    plain_file = mock_vault_env / "state" / "immigration.md"
    age_file.write_text("encrypted-data")
    plain_file.write_text("---\nprevious: good data\n---\n# Prior Content")

    def bad_yaml_decrypt(key, infile, outfile):
        outfile.write_text("NO YAML FRONTMATTER HERE\nsome garbage")
        return True

    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("scripts.vault.age_decrypt", side_effect=bad_yaml_decrypt):
        with pytest.raises(SystemExit):
            vault.do_decrypt()

    captured = capsys.readouterr()
    assert "frontmatter" in captured.err.lower()
    assert plain_file.exists()
    assert "Prior Content" in plain_file.read_text()


def test_decrypt_failure_restores_backup(mock_vault_env, capsys):
    """If age_decrypt returns False, backup is restored."""
    age_file = mock_vault_env / "state" / "immigration.md.age"
    plain_file = mock_vault_env / "state" / "immigration.md"
    age_file.write_text("encrypted-data")
    plain_file.write_text("---\nprevious: good data\n---\n# Prior Content")

    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("scripts.vault.age_decrypt", return_value=False):
        with pytest.raises(SystemExit):
            vault.do_decrypt()

    captured = capsys.readouterr()
    assert "Failed to decrypt" in captured.err
    assert plain_file.exists()
    assert "Prior Content" in plain_file.read_text()


def test_decrypt_failure_no_backup_logs_restore_failed(mock_vault_env, capsys):
    """If age_decrypt fails and no prior plaintext exists, INTEGRITY_RESTORE_FAILED is logged."""
    age_file = mock_vault_env / "state" / "immigration.md.age"
    plain_file = mock_vault_env / "state" / "immigration.md"
    age_file.write_text("encrypted-data")
    # No plain_file — first decrypt ever, no .bak will be created

    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("scripts.vault.age_decrypt", return_value=False):
        with pytest.raises(SystemExit):
            vault.do_decrypt()

    captured = capsys.readouterr()
    # No backup available — warning must say so
    assert "No backup available" in captured.err
    # Original .age file must be untouched
    assert age_file.exists()
    assert not plain_file.exists()


def test_decrypt_bak_creation_is_atomic(mock_vault_env, tmp_path):
    """A partial .bak.tmp left by a previous crash should be overwritten cleanly."""
    age_file = mock_vault_env / "state" / "immigration.md.age"
    plain_file = mock_vault_env / "state" / "immigration.md"
    bak_tmp = mock_vault_env / "state" / "immigration.md.bak.tmp"
    age_file.write_text("encrypted-data")
    plain_file.write_text("---\ncurrent: real data\n---\n# Current")
    # Simulate a stale partial .bak.tmp from a prior crashed session
    bak_tmp.write_text("PARTIAL GARBAGE")

    def good_decrypt(key, infile, outfile):
        outfile.write_text("---\nnew: data\n---\n# New")
        return True

    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("scripts.vault.age_decrypt", side_effect=good_decrypt):
        vault.do_decrypt()

    # .bak.tmp should be gone (replaced atomically by .bak, then .bak cleaned on success)
    assert not bak_tmp.exists()
    # New decrypted content should be in place
    assert plain_file.read_text().startswith("---")


def test_encrypt_cleans_up_bak_on_success(mock_vault_env, capsys):
    """Successful encryption must remove any orphaned .bak file."""
    plain_file = mock_vault_env / "state" / "immigration.md"
    age_file = mock_vault_env / "state" / "immigration.md.age"
    bak_file = mock_vault_env / "state" / "immigration.md.bak"
    plain_file.write_text("---\ndata: real\n---\n# Content" + " x" * 500)
    age_file.write_text("x" * 500)  # similar size — passes integrity check
    bak_file.write_text("---\ndata: old\n---\n# Old Content")
    (mock_vault_env / ".artha-decrypted").touch()
    (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

    def good_encrypt(pubkey, infile, outfile):
        outfile.write_text("encrypted")
        return True

    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("scripts.vault.get_public_key", return_value="age1mock"), \
         patch("scripts.vault.age_encrypt", side_effect=good_encrypt):
        vault.do_encrypt()

    assert not bak_file.exists()
    assert not plain_file.exists()
    assert age_file.exists()


def test_restore_bak_rejects_corrupt_backup(mock_vault_env, capsys):
    """_restore_bak must reject an empty or invalid .bak and log INTEGRITY_RESTORE_FAILED."""
    age_file = mock_vault_env / "state" / "immigration.md.age"
    plain_file = mock_vault_env / "state" / "immigration.md"
    bak_file = mock_vault_env / "state" / "immigration.md.bak"
    age_file.write_text("encrypted-data")
    # Pre-seed a corrupt (empty) .bak and no current plain_file
    bak_file.write_text("")  # corrupt/empty backup

    def bad_decrypt(key, infile, outfile):
        outfile.write_text("")  # empty
        return True

    with patch("scripts.vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("scripts.vault.age_decrypt", side_effect=bad_decrypt):
        with pytest.raises(SystemExit):
            vault.do_decrypt()

    captured = capsys.readouterr()
    # Should warn that backup is empty, not silently restore garbage
    assert "empty" in captured.err.lower() or "backup" in captured.err.lower()
    # plain_file must NOT exist (we did not restore the corrupt backup)
    assert not plain_file.exists()


# ---------------------------------------------------------------------------
# GFS Vault Backup — Tier logic (§8.5.2)
# ---------------------------------------------------------------------------

class TestGFSTierLogic:
    """_get_backup_tier must promote dates to the correct tier."""

    def test_daily_monday(self):
        assert vault._get_backup_tier(date(2026, 3, 9)) == "daily"   # Monday

    def test_daily_saturday(self):
        assert vault._get_backup_tier(date(2026, 3, 14)) == "daily"  # Saturday

    def test_weekly_sunday_mid_month(self):
        # Sunday that is NOT the last day of the month
        assert vault._get_backup_tier(date(2026, 3, 8)) == "weekly"

    def test_monthly_beats_sunday(self):
        # March 29 2026 is a Sunday AND the last day of the month (30-day month)
        # Actually March has 31 days. Let's use April 26 2026 (last Sunday of April)
        # April has 30 days. April 30 2026 is a Thursday, not last Sunday.
        # Use Feb 28 2027 (non-leap): Mon; last day of Feb. monthly wins.
        assert vault._get_backup_tier(date(2027, 2, 28)) == "monthly"

    def test_monthly_last_day_jan(self):
        assert vault._get_backup_tier(date(2026, 1, 31)) == "monthly"

    def test_monthly_last_day_april(self):
        assert vault._get_backup_tier(date(2026, 4, 30)) == "monthly"

    def test_monthly_feb28_non_leap(self):
        assert vault._get_backup_tier(date(2025, 2, 28)) == "monthly"

    def test_monthly_feb29_leap(self):
        assert vault._get_backup_tier(date(2028, 2, 29)) == "monthly"

    def test_yearly_dec31(self):
        assert vault._get_backup_tier(date(2025, 12, 31)) == "yearly"

    def test_yearly_beats_monthly_beats_sunday(self):
        # Dec 31 2023 is a Sunday AND year-end AND month-end: yearly wins
        assert vault._get_backup_tier(date(2023, 12, 31)) == "yearly"

    def test_not_daily_nov30(self):
        # Nov 30 is month-end regardless of weekday
        assert vault._get_backup_tier(date(2026, 11, 30)) == "monthly"


# ---------------------------------------------------------------------------
# GFS Vault Backup — Manifest
# ---------------------------------------------------------------------------

class TestManifest:
    """_load_manifest / _save_manifest round-trip and error handling."""

    def test_load_returns_empty_when_missing(self, mock_vault_env):
        m = vault._load_manifest()
        assert m == {"last_validate": None, "snapshots": {}}

    def test_save_and_load_round_trip(self, mock_vault_env):
        manifest = {"snapshots": {"daily/2026-03-13.zip": {"sha256": "abc"}}, "last_validate": None}
        vault._save_manifest(manifest)
        loaded = vault._load_manifest()
        assert loaded["snapshots"]["daily/2026-03-13.zip"]["sha256"] == "abc"

    def test_load_returns_empty_on_corrupt_json(self, mock_vault_env):
        vault.BACKUP_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        vault.BACKUP_MANIFEST.write_text("{ not valid json }")
        m = vault._load_manifest()
        assert m == {"last_validate": None, "snapshots": {}}

    def test_save_is_atomic(self, mock_vault_env):
        vault._save_manifest({"snapshots": {}, "last_validate": None})
        tmp = Path(str(vault.BACKUP_MANIFEST) + ".tmp")
        assert not tmp.exists()
        assert vault.BACKUP_MANIFEST.exists()


# ---------------------------------------------------------------------------
# GFS Vault Backup — _backup_snapshot
# ---------------------------------------------------------------------------

class TestBackupSnapshot:
    """_backup_snapshot creates a single ZIP per GFS tier-day, updates outer manifest."""

    def _seed_age_files(self, mock_vault_env, domains=("immigration", "finance")):
        import yaml as _yaml
        state_files = [{"name": d, "sensitive": True} for d in domains]
        profile = {"backup": {"state_files": state_files, "config_files": []}}
        profile_path = mock_vault_env / "config" / "user_profile.yaml"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(_yaml.dump(profile), encoding="utf-8")
        for d in domains:
            (mock_vault_env / "state" / f"{d}.md.age").write_bytes(b"x" * 500)

    def test_snapshot_creates_zip_in_daily_tier(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        count = vault._backup_snapshot(today=date(2026, 3, 9))  # Monday → daily
        assert count == 2
        assert (vault.BACKUP_DIR / "daily" / "2026-03-09.zip").exists()

    def test_snapshot_sunday_goes_to_weekly(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        vault._backup_snapshot(today=date(2026, 3, 8))
        assert (vault.BACKUP_DIR / "weekly" / "2026-03-08.zip").exists()
        assert not (vault.BACKUP_DIR / "daily" / "2026-03-08.zip").exists()

    def test_snapshot_month_end_goes_to_monthly(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        vault._backup_snapshot(today=date(2026, 2, 28))
        assert (vault.BACKUP_DIR / "monthly" / "2026-02-28.zip").exists()

    def test_snapshot_dec31_goes_to_yearly(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        vault._backup_snapshot(today=date(2025, 12, 31))
        assert (vault.BACKUP_DIR / "yearly" / "2025-12-31.zip").exists()

    def test_snapshot_writes_outer_manifest_with_checksum(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        vault._backup_snapshot(today=date(2026, 3, 9))
        m = vault._load_manifest()
        key = "daily/2026-03-09.zip"
        assert key in m["snapshots"]
        meta = m["snapshots"][key]
        assert len(meta["sha256"]) == 64
        assert meta["tier"] == "daily"
        assert meta["date"] == "2026-03-09"
        assert meta["file_count"] == 2

    def test_snapshot_zip_contains_internal_manifest(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        vault._backup_snapshot(today=date(2026, 3, 9))
        zip_path = vault.BACKUP_DIR / "daily" / "2026-03-09.zip"
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            assert "manifest.json" in zf.namelist()

    def test_snapshot_is_atomic_no_tmp_remains(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        vault._backup_snapshot(today=date(2026, 3, 9))
        assert list(vault.BACKUP_DIR.rglob("*.tmp")) == []

    def test_snapshot_skips_missing_age_file(self, mock_vault_env):
        import yaml as _yaml
        profile = {"backup": {"state_files": [{"name": "immigration", "sensitive": True}], "config_files": []}}
        (mock_vault_env / "config" / "user_profile.yaml").parent.mkdir(parents=True, exist_ok=True)
        (mock_vault_env / "config" / "user_profile.yaml").write_text(_yaml.dump(profile))
        (mock_vault_env / "state" / "immigration.md.age").write_bytes(b"x" * 200)
        count = vault._backup_snapshot(today=date(2026, 3, 9))
        assert count == 1

    def test_snapshot_returns_zero_when_no_files(self, mock_vault_env):
        count = vault._backup_snapshot(today=date(2026, 3, 9))
        assert count == 0


# ---------------------------------------------------------------------------
# GFS Vault Backup — Pruning
# ---------------------------------------------------------------------------

class TestPruning:
    """_prune_backups enforces ZIP retention limits per GFS tier."""

    def _create_daily_zips(self, n_days):
        """Create n_days worth of daily ZIP files in vault.BACKUP_DIR/daily."""
        tier_dir = vault.BACKUP_DIR / "daily"
        tier_dir.mkdir(parents=True, exist_ok=True)
        manifest = vault._load_manifest()
        for i in range(n_days):
            d = date(2026, 3, i + 1)
            z = tier_dir / f"{d.isoformat()}.zip"
            with zipfile.ZipFile(str(z), "w") as zf:
                zf.writestr("manifest.json", json.dumps({"date": d.isoformat(), "tier": "daily", "files": {}}))
            manifest["snapshots"][f"daily/{z.name}"] = {
                "date": d.isoformat(), "tier": "daily",
                "file_count": 1, "sha256": "x" * 64, "size": 100,
            }
        vault._save_manifest(manifest)

    def test_prune_keeps_exactly_n(self, mock_vault_env):
        self._create_daily_zips(10)
        vault._prune_backups("daily", 7)
        remaining = sorted((vault.BACKUP_DIR / "daily").glob("*.zip"))
        assert len(remaining) == 7
        assert remaining[-1].stem == "2026-03-10"
        assert remaining[0].stem == "2026-03-04"

    def test_prune_does_nothing_when_under_limit(self, mock_vault_env):
        self._create_daily_zips(5)
        vault._prune_backups("daily", 7)
        remaining = list((vault.BACKUP_DIR / "daily").glob("*.zip"))
        assert len(remaining) == 5

    def test_prune_removes_manifest_entries(self, mock_vault_env):
        self._create_daily_zips(10)
        vault._prune_backups("daily", 7)
        loaded = vault._load_manifest()
        assert len([k for k in loaded["snapshots"] if k.startswith("daily/")]) == 7

    def test_yearly_not_pruned_because_none_keep(self, mock_vault_env):
        # GFS_RETENTION["yearly"] is None — the caller loop guards against calling prune
        assert vault.GFS_RETENTION["yearly"] is None


# ---------------------------------------------------------------------------
# GFS Vault Backup — do_encrypt triggers snapshot
# ---------------------------------------------------------------------------

class TestEncryptTriggersBackup:
    """do_encrypt must call _backup_snapshot after successful encryption."""

    def test_encrypt_creates_backup_zip(self, mock_vault_env, capsys):
        plain = mock_vault_env / "state" / "immigration.md"
        age   = mock_vault_env / "state" / "immigration.md.age"
        plain.write_text("---\ndata: ok\n---\n" + "x " * 300)
        age.write_text("x" * 500)
        (mock_vault_env / ".artha-decrypted").touch()
        (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

        def good_encrypt(pubkey, infile, outfile):
            outfile.write_bytes(b"encrypted" + b"x" * 400)
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_public_key", return_value="age1mock"), \
             patch("scripts.vault.age_encrypt", side_effect=good_encrypt):
            vault.do_encrypt()

        zip_files = list(vault.BACKUP_DIR.rglob("*.zip"))
        assert len(zip_files) >= 1

    def test_encrypt_backup_not_called_on_error(self, mock_vault_env, capsys):
        """If encryption fails, backup must not be taken."""
        plain = mock_vault_env / "state" / "immigration.md"
        age   = mock_vault_env / "state" / "immigration.md.age"
        plain.write_text("---\ndata: ok\n---\n" + "x " * 300)
        age.write_text("x" * 500)
        (mock_vault_env / ".artha-decrypted").touch()
        (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_public_key", return_value="age1mock"), \
             patch("scripts.vault.age_encrypt", return_value=False), \
             patch("scripts.vault._backup_snapshot") as mock_snapshot:
            with pytest.raises(SystemExit):
                vault.do_encrypt()
            mock_snapshot.assert_not_called()


# ---------------------------------------------------------------------------
# GFS Vault Backup — Restore validation
# ---------------------------------------------------------------------------

class TestValidateBackup:
    """do_validate_backup — ZIP-based validation: happy path and all failure modes."""

    def _seed_zip(self, tier="daily", date_str="2026-03-13",
                  source_type="state_encrypted", restore_path="state/immigration.md.age",
                  arc_path="state/immigration.md.age", name="immigration",
                  content=b"encrypted-content"):
        """Create a ZIP and register it in the outer manifest."""
        zip_path = _create_test_zip(vault.BACKUP_DIR, tier, date_str, {
            arc_path: (content, source_type, restore_path, name),
        })
        sha256 = vault._file_sha256(zip_path)
        m = vault._load_manifest()
        m["snapshots"][f"{tier}/{date_str}.zip"] = {
            "created": f"{date_str}T00:00:00+00:00", "date": date_str, "tier": tier,
            "sha256": sha256, "size": zip_path.stat().st_size, "file_count": 1,
        }
        vault._save_manifest(m)
        return zip_path

    def test_validate_ok_happy_path(self, mock_vault_env, capsys):
        self._seed_zip()

        def good_decrypt(key, infile, outfile):
            outfile.write_text("---\nschema_version: '1.0'\n---\n# Immigration\n" + "word " * 50)
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", side_effect=good_decrypt):
            vault.do_validate_backup()

        out = capsys.readouterr().out
        assert "✓" in out
        assert "valid" in out.lower()
        assert vault._load_manifest()["last_validate"] is not None

    def test_validate_fails_checksum_mismatch(self, mock_vault_env, capsys):
        zip_path = self._seed_zip()
        zip_path.write_bytes(b"corrupted zip contents!!")  # invalidates outer sha256

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             pytest.raises(SystemExit):
            vault.do_validate_backup()

        assert "CHECKSUM" in capsys.readouterr().out

    def test_validate_fails_missing_file_in_zip(self, mock_vault_env, capsys):
        """Internal manifest references a file not present in the ZIP."""
        tier_dir = vault.BACKUP_DIR / "daily"
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
            # file body intentionally omitted from the ZIP
        sha256 = vault._file_sha256(zip_path)
        m = vault._load_manifest()
        m["snapshots"]["daily/2026-03-13.zip"] = {
            "date": "2026-03-13", "tier": "daily", "sha256": sha256,
            "size": zip_path.stat().st_size, "file_count": 1,
        }
        vault._save_manifest(m)

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             pytest.raises(SystemExit):
            vault.do_validate_backup()

        assert "MISSING" in capsys.readouterr().out

    def test_validate_fails_decrypt_error(self, mock_vault_env, capsys):
        self._seed_zip()

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", return_value=False), \
             pytest.raises(SystemExit):
            vault.do_validate_backup()

        assert "DECRYPT" in capsys.readouterr().out

    def test_validate_fails_empty_content(self, mock_vault_env, capsys):
        self._seed_zip()

        def empty_decrypt(key, infile, outfile):
            outfile.write_text("")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", side_effect=empty_decrypt), \
             pytest.raises(SystemExit):
            vault.do_validate_backup()

        assert "EMPTY" in capsys.readouterr().out

    def test_validate_fails_no_yaml_frontmatter(self, mock_vault_env, capsys):
        self._seed_zip()

        def no_yaml_decrypt(key, infile, outfile):
            outfile.write_text("NO FRONTMATTER HERE\n" + "word " * 50)
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", side_effect=no_yaml_decrypt), \
             pytest.raises(SystemExit):
            vault.do_validate_backup()

        assert "YAML" in capsys.readouterr().out

    def test_validate_fails_too_short(self, mock_vault_env, capsys):
        self._seed_zip()

        def short_decrypt(key, infile, outfile):
            outfile.write_text("---\ndata: ok\n---\nfew words only")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", side_effect=short_decrypt), \
             pytest.raises(SystemExit):
            vault.do_validate_backup()

        assert "SHORT" in capsys.readouterr().out.upper()

    def test_validate_domain_filter(self, mock_vault_env):
        """--domain validates only matching entries inside the ZIP."""
        zip_path = _create_test_zip(vault.BACKUP_DIR, "daily", "2026-03-13", {
            "state/immigration.md.age": (b"enc-imm", "state_encrypted", "state/immigration.md.age", "immigration"),
            "state/finance.md.age":     (b"enc-fin", "state_encrypted", "state/finance.md.age",     "finance"),
        })
        sha256 = vault._file_sha256(zip_path)
        m = vault._load_manifest()
        m["snapshots"]["daily/2026-03-13.zip"] = {
            "date": "2026-03-13", "tier": "daily", "sha256": sha256,
            "size": zip_path.stat().st_size, "file_count": 2,
        }
        vault._save_manifest(m)

        def track_decrypt(key, infile, outfile):
            outfile.write_text("---\nschema_version: '1.0'\n---\n# Content\n" + "word " * 50)
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", side_effect=track_decrypt) as mock_d:
            vault.do_validate_backup(domain="immigration")
            assert mock_d.call_count == 1

    def test_validate_no_backups_exits_gracefully(self, mock_vault_env, capsys):
        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"):
            vault.do_validate_backup()  # should not raise

        assert "No backups" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# GFS Vault Backup — do_backup_status
# ---------------------------------------------------------------------------

class TestBackupStatus:
    def test_status_shows_never_when_no_backups(self, mock_vault_env, capsys):
        vault.do_backup_status()
        out = capsys.readouterr().out
        assert "NEVER" in out or "No backups" in out

    def test_status_shows_tiers(self, mock_vault_env, capsys):
        m = vault._load_manifest()
        for tier, d in [("daily", "2026-03-13"), ("weekly", "2026-03-08"),
                        ("monthly", "2026-02-28"), ("yearly", "2025-12-31")]:
            (vault.BACKUP_DIR / tier).mkdir(parents=True, exist_ok=True)
            z = vault.BACKUP_DIR / tier / f"{d}.zip"
            with zipfile.ZipFile(str(z), "w") as zf:
                zf.writestr("manifest.json", json.dumps({"date": d, "tier": tier, "files": {}}))
            m["snapshots"][f"{tier}/{d}.zip"] = {
                "date": d, "tier": tier, "file_count": 5,
                "sha256": "a" * 64, "size": 1024,
            }
        vault._save_manifest(m)
        vault.do_backup_status()
        out = capsys.readouterr().out
        assert "DAILY" in out
        assert "WEEKLY" in out
        assert "MONTHLY" in out
        assert "YEARLY" in out

    def test_status_warns_when_validation_overdue(self, mock_vault_env, capsys):
        from datetime import datetime, timedelta, timezone
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
        m = vault._load_manifest()
        m["last_validate"] = old_ts
        vault._save_manifest(m)

        vault.do_backup_status()
        out = capsys.readouterr().out
        assert "overdue" in out.lower() or "⚠" in out


# ---------------------------------------------------------------------------
# Backup Registry
# ---------------------------------------------------------------------------

class TestBackupRegistry:
    """_load_backup_registry reads user_profile.yaml and returns correct entries."""

    def _write_profile(self, mock_vault_env, state_files, config_files=None):
        profile = {
            "backup": {
                "state_files": state_files,
                "config_files": config_files or [],
            }
        }
        import yaml as _yaml
        profile_path = mock_vault_env / "config" / "user_profile.yaml"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(_yaml.dump(profile), encoding="utf-8")

    def test_loads_sensitive_state_entries(self, mock_vault_env):
        self._write_profile(mock_vault_env, [
            {"name": "finance", "sensitive": True},
            {"name": "health", "sensitive": True},
        ])
        entries = vault._load_backup_registry()
        names = {e["name"] for e in entries}
        assert "finance" in names
        assert "health" in names
        for e in entries:
            assert e["source_type"] == "state_encrypted"
            assert str(e["source_path"]).endswith(".md.age")
            assert e["restore_path"].endswith(".md.age")

    def test_loads_plain_state_entries(self, mock_vault_env):
        self._write_profile(mock_vault_env, [
            {"name": "goals", "sensitive": False},
            {"name": "home", "sensitive": False},
        ])
        entries = vault._load_backup_registry()
        for e in entries:
            assert e["source_type"] == "state_plain"
            assert str(e["source_path"]).endswith(".md")
            assert e["restore_path"] == f"state/{e['name']}.md"

    def test_loads_config_entries(self, mock_vault_env):
        self._write_profile(mock_vault_env, [], ["config/user_profile.yaml", "config/routing.yaml"])
        entries = vault._load_backup_registry()
        names = {e["name"] for e in entries}
        assert "cfg__config__user_profile_yaml" in names
        assert "cfg__config__routing_yaml" in names
        for e in entries:
            assert e["source_type"] == "config"
            assert e["restore_path"].startswith("config/")

    def test_fallback_when_no_profile(self, mock_vault_env):
        # No user_profile.yaml → falls back to SENSITIVE_FILES
        entries = vault._load_backup_registry()
        entry_names = {e["name"] for e in entries}
        for name in vault.SENSITIVE_FILES:
            assert name in entry_names
        for e in entries:
            assert e["source_type"] == "state_encrypted"

    def test_fallback_when_no_backup_section(self, mock_vault_env):
        import yaml as _yaml
        profile_path = mock_vault_env / "config" / "user_profile.yaml"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(_yaml.dump({"family": {"name": "Test"}}), encoding="utf-8")
        entries = vault._load_backup_registry()
        # Falls back to SENSITIVE_FILES
        entry_names = {e["name"] for e in entries}
        for name in vault.SENSITIVE_FILES:
            assert name in entry_names

    def test_all_31_state_files_present_in_full_registry(self, mock_vault_env):
        """A full registry with 31 state + 4 config entries is loaded correctly."""
        state_files = (
            [{"name": n, "sensitive": True}
             for n in ["immigration","finance","insurance","estate","health",
                       "vehicle","contacts","occasions","audit"]] +
            [{"name": n, "sensitive": False}
             for n in ["boundary","calendar","comms","dashboard","decisions",
                       "digital","employment","goals","health-check",
                       "health-metrics","home","kids","learning","memory",
                       "onenote_progress","open_items","scenarios","shopping",
                       "social","travel","work-calendar","plaid-privacy-research"]]
        )
        config_files = [
            "config/user_profile.yaml", "config/routing.yaml",
            "config/connectors.yaml",   "config/artha_config.yaml",
        ]
        self._write_profile(mock_vault_env, state_files, config_files)
        entries = vault._load_backup_registry()
        assert len(entries) == len(state_files) + len(config_files)
        sensitive_count = sum(1 for e in entries if e["source_type"] == "state_encrypted")
        plain_count     = sum(1 for e in entries if e["source_type"] == "state_plain")
        config_count    = sum(1 for e in entries if e["source_type"] == "config")
        assert sensitive_count == 9
        assert plain_count     == 22
        assert config_count    == 4


# ---------------------------------------------------------------------------
# Snapshot: plain state + config file handling
# ---------------------------------------------------------------------------

class TestBackupSnapshotComprehensive:
    """_backup_snapshot encrypts plain/config on-the-fly and stores them inside the ZIP."""

    def _write_profile(self, mock_vault_env, state_files, config_files=None):
        import yaml as _yaml
        profile = {"backup": {"state_files": state_files, "config_files": config_files or []}}
        p = mock_vault_env / "config" / "user_profile.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_yaml.dump(profile), encoding="utf-8")

    def test_plain_state_file_is_encrypted_into_backup(self, mock_vault_env):
        self._write_profile(mock_vault_env, [{"name": "goals", "sensitive": False}])
        (mock_vault_env / "state" / "goals.md").write_text("---\n# Goals\nword " * 40)
        today = date(2026, 3, 9)

        def fake_encrypt(pk, src, dst):
            dst.write_bytes(b"encrypted-goals")
            return True

        with patch("scripts.vault.get_public_key", return_value="age1mock"), \
             patch("scripts.vault.age_encrypt", side_effect=fake_encrypt):
            count = vault._backup_snapshot(today=today)

        assert count == 1
        zip_path = vault.BACKUP_DIR / "daily" / "2026-03-09.zip"
        assert zip_path.exists()
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        assert "state/goals.md.age" in internal["files"]
        meta = internal["files"]["state/goals.md.age"]
        assert meta["source_type"] == "state_plain"
        assert meta["restore_path"] == "state/goals.md"

    def test_config_file_is_encrypted_into_backup(self, mock_vault_env):
        self._write_profile(mock_vault_env, [], ["config/artha_config.yaml"])
        (mock_vault_env / "config" / "artha_config.yaml").write_text("todo_lists:\n  general: abc123\n")
        today = date(2026, 3, 9)

        def fake_encrypt(pk, src, dst):
            dst.write_bytes(b"encrypted-config")
            return True

        with patch("scripts.vault.get_public_key", return_value="age1mock"), \
             patch("scripts.vault.age_encrypt", side_effect=fake_encrypt):
            count = vault._backup_snapshot(today=today)

        assert count == 1
        zip_path = vault.BACKUP_DIR / "daily" / "2026-03-09.zip"
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        assert "config/artha_config.yaml.age" in internal["files"]
        meta = internal["files"]["config/artha_config.yaml.age"]
        assert meta["source_type"] == "config"
        assert meta["restore_path"] == "config/artha_config.yaml"

    def test_plain_file_skipped_when_no_pubkey(self, mock_vault_env):
        self._write_profile(mock_vault_env, [{"name": "goals", "sensitive": False}])
        (mock_vault_env / "state" / "goals.md").write_text("---\n# Goals\n")
        with patch("scripts.vault.get_public_key", side_effect=SystemExit(1)):
            count = vault._backup_snapshot(today=date(2026, 3, 9))
        assert count == 0

    def test_mixed_registry_backs_up_all_types(self, mock_vault_env):
        """Encrypted state, plain state, and config all land inside the same ZIP."""
        self._write_profile(
            mock_vault_env,
            [{"name": "finance", "sensitive": True}, {"name": "goals", "sensitive": False}],
            ["config/artha_config.yaml"],
        )
        (mock_vault_env / "state" / "finance.md.age").write_bytes(b"x" * 300)
        (mock_vault_env / "state" / "goals.md").write_text("---\n# Goals\n")
        (mock_vault_env / "config" / "artha_config.yaml").write_text("todo_lists:\n  general: abc\n")

        def fake_encrypt(pk, src, dst):
            dst.write_bytes(b"encrypted")
            return True

        with patch("scripts.vault.get_public_key", return_value="age1mock"), \
             patch("scripts.vault.age_encrypt", side_effect=fake_encrypt):
            count = vault._backup_snapshot(today=date(2026, 3, 9))

        assert count == 3
        zip_path = vault.BACKUP_DIR / "daily" / "2026-03-09.zip"
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        assert "state/finance.md.age" in internal["files"]
        assert "state/goals.md.age" in internal["files"]
        assert "config/artha_config.yaml.age" in internal["files"]

    def test_manifest_entry_has_restore_path_and_source_type(self, mock_vault_env):
        self._write_profile(mock_vault_env, [{"name": "immigration", "sensitive": True}])
        (mock_vault_env / "state" / "immigration.md.age").write_bytes(b"x" * 200)
        vault._backup_snapshot(today=date(2026, 3, 9))
        m = vault._load_manifest()
        key = "daily/2026-03-09.zip"
        assert key in m["snapshots"]
        assert m["snapshots"][key]["file_count"] == 1
        zip_path = vault.BACKUP_DIR / key
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            internal = json.loads(zf.read("manifest.json"))
        meta = internal["files"]["state/immigration.md.age"]
        assert meta["source_type"]  == "state_encrypted"
        assert meta["restore_path"] == "state/immigration.md.age"
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GFS restore — do_restore (catalog) and do_install (explicit ZIP)
# ---------------------------------------------------------------------------

class TestRestore:
    """do_restore finds the right ZIP from the catalog and reconstructs files."""

    def _seed_zip(self, tier="daily", date_str="2026-03-14"):
        """Create a ZIP with all three source types and register in outer manifest."""
        files_dict = {
            "state/immigration.md.age": (b"enc-imm", "state_encrypted", "state/immigration.md.age", "immigration"),
            "state/goals.md.age":       (b"enc-goals", "state_plain",    "state/goals.md",           "goals"),
            "config/user_profile.yaml.age": (b"enc-cfg", "config",       "config/user_profile.yaml", "cfg_user_profile"),
        }
        zip_path = _create_test_zip(vault.BACKUP_DIR, tier, date_str, files_dict)
        sha256 = vault._file_sha256(zip_path)
        m = vault._load_manifest()
        m["snapshots"][f"{tier}/{date_str}.zip"] = {
            "created": f"{date_str}T00:00:00+00:00", "date": date_str, "tier": tier,
            "sha256": sha256, "size": zip_path.stat().st_size, "file_count": 3,
        }
        vault._save_manifest(m)
        return zip_path

    def test_restore_dry_run_lists_files_without_writing(self, mock_vault_env, capsys):
        self._seed_zip()
        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", return_value=True):
            vault.do_restore(date_str="2026-03-14", dry_run=True)

        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "state/immigration.md.age" in out
        assert not (mock_vault_env / "state" / "immigration.md.age").exists()

    def test_restore_state_encrypted_copies_age_file(self, mock_vault_env):
        self._seed_zip()
        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", side_effect=lambda k, s, d: d.write_bytes(b"dec") or True):
            vault.do_restore(date_str="2026-03-14")

        restored = mock_vault_env / "state" / "immigration.md.age"
        assert restored.exists()
        assert restored.read_bytes() == b"enc-imm"  # state_encrypted: copied directly

    def test_restore_state_plain_decrypts_file(self, mock_vault_env):
        self._seed_zip()

        def fake_decrypt(key, src, dst):
            dst.write_text("---\n# Goals\n")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", side_effect=fake_decrypt):
            vault.do_restore(date_str="2026-03-14")

        restored = mock_vault_env / "state" / "goals.md"
        assert restored.exists()
        assert restored.read_text() == "---\n# Goals\n"

    def test_restore_config_decrypts_to_config_dir(self, mock_vault_env):
        self._seed_zip()

        def fake_decrypt(key, src, dst):
            dst.write_text("age_recipient: age1xxx\n")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", side_effect=fake_decrypt):
            vault.do_restore(date_str="2026-03-14")

        restored = mock_vault_env / "config" / "user_profile.yaml"
        assert restored.exists()
        assert "age1xxx" in restored.read_text()

    def test_restore_fails_on_checksum_mismatch(self, mock_vault_env, capsys):
        # Create a ZIP where the internal manifest has a wrong sha256 for a file
        tier_dir = vault.BACKUP_DIR / "daily"
        tier_dir.mkdir(parents=True, exist_ok=True)
        zip_path = tier_dir / "2026-03-14.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("state/immigration.md.age", b"correct-content")
            zf.writestr("manifest.json", json.dumps({
                "artha_backup_version": "2", "date": "2026-03-14", "tier": "daily",
                "files": {"state/immigration.md.age": {
                    "sha256": "a" * 64,  # intentionally wrong
                    "size": 15, "source_type": "state_encrypted",
                    "restore_path": "state/immigration.md.age", "name": "immigration",
                }},
            }))
        sha256 = vault._file_sha256(zip_path)
        m = vault._load_manifest()
        m["snapshots"]["daily/2026-03-14.zip"] = {
            "date": "2026-03-14", "tier": "daily", "sha256": sha256,
            "size": zip_path.stat().st_size, "file_count": 1,
        }
        vault._save_manifest(m)

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             pytest.raises(SystemExit):
            vault.do_restore(date_str="2026-03-14")

        assert "CHECKSUM" in capsys.readouterr().out

    def test_restore_no_backups_exits_gracefully(self, mock_vault_env, capsys):
        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"):
            vault.do_restore(date_str="2026-03-14")

        assert "No backups" in capsys.readouterr().out


class TestInstall:
    """do_install restores a system from an explicit ZIP file path (cold-start)."""

    def _make_zip(self, tmp_path):
        """Create a temporary ZIP with all three file types."""
        files_dict = {
            "state/immigration.md.age": (b"enc-imm", "state_encrypted", "state/immigration.md.age", "immigration"),
            "state/goals.md.age":       (b"enc-goals", "state_plain",    "state/goals.md",           "goals"),
            "config/artha_config.yaml.age": (b"enc-cfg", "config",       "config/artha_config.yaml", "artha_config"),
        }
        tier_dir = tmp_path / "export"
        return _create_test_zip(tier_dir, "daily", "2026-03-14", files_dict)

    def test_install_dry_run_previews_without_writing(self, mock_vault_env, tmp_path, capsys):
        zip_path = self._make_zip(tmp_path)
        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", return_value=True):
            vault.do_install(str(zip_path), dry_run=True)

        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert not (mock_vault_env / "state" / "immigration.md.age").exists()

    def test_install_state_encrypted_copied_directly(self, mock_vault_env, tmp_path):
        zip_path = self._make_zip(tmp_path)
        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", side_effect=lambda k, s, d: d.write_bytes(b"dec") or True):
            vault.do_install(str(zip_path))

        restored = mock_vault_env / "state" / "immigration.md.age"
        assert restored.exists()
        assert restored.read_bytes() == b"enc-imm"  # not decrypted — copied as-is

    def test_install_state_plain_decrypted(self, mock_vault_env, tmp_path):
        zip_path = self._make_zip(tmp_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("---\n# Goals\n")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", side_effect=fake_decrypt):
            vault.do_install(str(zip_path))

        restored = mock_vault_env / "state" / "goals.md"
        assert restored.read_text() == "---\n# Goals\n"

    def test_install_config_decrypted_to_config_dir(self, mock_vault_env, tmp_path):
        zip_path = self._make_zip(tmp_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("todo_lists:\n  general: id123\n")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", side_effect=fake_decrypt):
            vault.do_install(str(zip_path))

        restored = mock_vault_env / "config" / "artha_config.yaml"
        assert "id123" in restored.read_text()

    def test_install_missing_zip_exits(self, mock_vault_env):
        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             pytest.raises(SystemExit):
            vault.do_install("/nonexistent/path/2026-03-14.zip")


# ---------------------------------------------------------------------------
# --data-only mode: restore and install skip config files
# ---------------------------------------------------------------------------

class TestDataOnlyRestore:
    """--data-only flag skips config source_type entries in both restore and install."""

    def _make_full_zip(self, backup_dir):
        """ZIP with all three source types."""
        files_dict = {
            "state/immigration.md.age": (b"enc-imm",   "state_encrypted", "state/immigration.md.age",  "immigration"),
            "state/goals.md.age":       (b"enc-goals",  "state_plain",    "state/goals.md",             "goals"),
            "config/routing.yaml.age":  (b"enc-cfg",    "config",         "config/routing.yaml",        "routing"),
        }
        return _create_test_zip(backup_dir, "daily", "2026-03-14", files_dict)

    def _register_zip(self, zip_path, tier="daily", date_str="2026-03-14"):
        sha256 = vault._file_sha256(zip_path)
        m = vault._load_manifest()
        m["snapshots"][f"{tier}/{date_str}.zip"] = {
            "created": f"{date_str}T00:00:00+00:00", "date": date_str, "tier": tier,
            "sha256": sha256, "size": zip_path.stat().st_size, "file_count": 3,
        }
        vault._save_manifest(m)

    # --- do_restore --data-only ---

    def test_restore_data_only_skips_config(self, mock_vault_env):
        """--data-only: state files restored, config file NOT written."""
        zip_path = self._make_full_zip(vault.BACKUP_DIR)
        self._register_zip(zip_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("---\n# Goals\n")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", side_effect=fake_decrypt):
            vault.do_restore(date_str="2026-03-14", data_only=True)

        assert (mock_vault_env / "state" / "immigration.md.age").exists()
        assert (mock_vault_env / "state" / "goals.md").exists()
        assert not (mock_vault_env / "config" / "routing.yaml").exists()

    def test_restore_full_includes_config(self, mock_vault_env):
        """Default (no --data-only): config file IS restored."""
        zip_path = self._make_full_zip(vault.BACKUP_DIR)
        self._register_zip(zip_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("---\n# Content\nsome: value\n")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", side_effect=fake_decrypt):
            vault.do_restore(date_str="2026-03-14")

        assert (mock_vault_env / "state" / "immigration.md.age").exists()
        assert (mock_vault_env / "config" / "routing.yaml").exists()

    def test_restore_data_only_dry_run_shows_scope(self, mock_vault_env, capsys):
        zip_path = self._make_full_zip(vault.BACKUP_DIR)
        self._register_zip(zip_path)

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", return_value=True):
            vault.do_restore(date_str="2026-03-14", dry_run=True, data_only=True)

        out = capsys.readouterr().out
        assert "state only" in out.lower()
        assert "config skipped" in out.lower()

    # --- do_install --data-only ---

    def test_install_data_only_skips_config(self, mock_vault_env, tmp_path):
        zip_path = self._make_full_zip(tmp_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("---\n# Goals\n")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", side_effect=fake_decrypt):
            vault.do_install(str(zip_path), data_only=True)

        assert (mock_vault_env / "state" / "immigration.md.age").exists()
        assert (mock_vault_env / "state" / "goals.md").exists()
        assert not (mock_vault_env / "config" / "routing.yaml").exists()

    def test_install_full_includes_config(self, mock_vault_env, tmp_path):
        zip_path = self._make_full_zip(tmp_path)

        def fake_decrypt(key, src, dst):
            dst.write_text("key: value\n")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("scripts.vault.get_private_key", return_value="mock-priv"), \
             patch("scripts.vault.age_decrypt", side_effect=fake_decrypt):
            vault.do_install(str(zip_path))

        assert (mock_vault_env / "config" / "routing.yaml").exists()

