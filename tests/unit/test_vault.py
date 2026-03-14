import pytest
import os
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import vault logic
import scripts.vault as vault

@pytest.fixture
def mock_vault_env(temp_artha_dir, monkeypatch):
    """Set up a mock environment for vault.py."""
    backup_dir = temp_artha_dir / "state" / "backups"
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
        assert m == {"files": {}, "last_validate": None}

    def test_save_and_load_round_trip(self, mock_vault_env):
        manifest = {"files": {"daily/foo-2026-03-13.md.age": {"sha256": "abc"}}, "last_validate": None}
        vault._save_manifest(manifest)
        loaded = vault._load_manifest()
        assert loaded["files"]["daily/foo-2026-03-13.md.age"]["sha256"] == "abc"

    def test_load_returns_empty_on_corrupt_json(self, mock_vault_env):
        # Write garbage to manifest
        vault.BACKUP_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        vault.BACKUP_MANIFEST.write_text("{ not valid json }")
        m = vault._load_manifest()
        assert m == {"files": {}, "last_validate": None}

    def test_save_is_atomic(self, mock_vault_env):
        # After save, no .tmp file should remain
        vault._save_manifest({"files": {}, "last_validate": None})
        tmp = Path(str(vault.BACKUP_MANIFEST) + ".tmp")
        assert not tmp.exists()
        assert vault.BACKUP_MANIFEST.exists()


# ---------------------------------------------------------------------------
# GFS Vault Backup — _backup_snapshot
# ---------------------------------------------------------------------------

class TestBackupSnapshot:
    """_backup_snapshot creates backup files, writes manifest, prunes correctly."""

    def _seed_age_files(self, mock_vault_env, domains=("immigration", "finance")):
        for d in domains:
            (mock_vault_env / "state" / f"{d}.md.age").write_bytes(b"x" * 500)

    def test_snapshot_creates_files_in_correct_tier(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        monday = date(2026, 3, 9)  # daily
        count = vault._backup_snapshot(today=monday)
        assert count == 2
        assert (vault.BACKUP_DIR / "daily" / "immigration-2026-03-09.md.age").exists()
        assert (vault.BACKUP_DIR / "daily" / "finance-2026-03-09.md.age").exists()

    def test_snapshot_sunday_goes_to_weekly(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        sunday = date(2026, 3, 8)
        vault._backup_snapshot(today=sunday)
        assert (vault.BACKUP_DIR / "weekly" / "immigration-2026-03-08.md.age").exists()
        assert not (vault.BACKUP_DIR / "daily" / "immigration-2026-03-08.md.age").exists()

    def test_snapshot_month_end_goes_to_monthly(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        vault._backup_snapshot(today=date(2026, 2, 28))
        assert (vault.BACKUP_DIR / "monthly" / "immigration-2026-02-28.md.age").exists()

    def test_snapshot_dec31_goes_to_yearly(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        vault._backup_snapshot(today=date(2025, 12, 31))
        assert (vault.BACKUP_DIR / "yearly" / "immigration-2025-12-31.md.age").exists()

    def test_snapshot_writes_manifest_with_checksum(self, mock_vault_env):
        self._seed_age_files(mock_vault_env)
        vault._backup_snapshot(today=date(2026, 3, 9))
        m = vault._load_manifest()
        key = "daily/immigration-2026-03-09.md.age"
        assert key in m["files"]
        meta = m["files"][key]
        assert len(meta["sha256"]) == 64
        assert meta["tier"] == "daily"
        assert meta["domain"] == "immigration"
        assert meta["date"] == "2026-03-09"

    def test_snapshot_is_atomic(self, mock_vault_env):
        """No .tmp file should remain after a successful snapshot."""
        self._seed_age_files(mock_vault_env)
        vault._backup_snapshot(today=date(2026, 3, 9))
        tmps = list(vault.BACKUP_DIR.rglob("*.tmp"))
        assert tmps == []

    def test_snapshot_skips_missing_age_file(self, mock_vault_env):
        # Only seed one domain
        (mock_vault_env / "state" / "immigration.md.age").write_bytes(b"x" * 200)
        count = vault._backup_snapshot(today=date(2026, 3, 9))
        assert count == 1

    def test_snapshot_returns_zero_when_no_age_files(self, mock_vault_env):
        count = vault._backup_snapshot(today=date(2026, 3, 9))
        assert count == 0


# ---------------------------------------------------------------------------
# GFS Vault Backup — Pruning
# ---------------------------------------------------------------------------

class TestPruning:
    """_prune_backups enforces retention limits."""

    def _create_daily_backups(self, domain, days):
        tier_dir = vault.BACKUP_DIR / "daily"
        tier_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for i in range(days):
            d = date(2026, 3, i + 1)
            f = tier_dir / f"{domain}-{d.isoformat()}.md.age"
            f.write_bytes(b"x" * 100)
            files.append(f)
        return files

    def test_prune_keeps_exactly_n(self, mock_vault_env):
        files = self._create_daily_backups("immigration", 10)
        vault._prune_backups("immigration", "daily", 7)
        remaining = sorted((vault.BACKUP_DIR / "daily").glob("immigration-*.md.age"))
        assert len(remaining) == 7
        # Most recent 7 kept
        assert remaining[-1].name == "immigration-2026-03-10.md.age"
        assert remaining[0].name == "immigration-2026-03-04.md.age"

    def test_prune_does_nothing_when_under_limit(self, mock_vault_env):
        self._create_daily_backups("immigration", 5)
        vault._prune_backups("immigration", "daily", 7)
        remaining = list((vault.BACKUP_DIR / "daily").glob("immigration-*.md.age"))
        assert len(remaining) == 5

    def test_prune_removes_manifest_entries(self, mock_vault_env):
        self._create_daily_backups("immigration", 10)
        # Populate manifest with all 10
        m = {"files": {}, "last_validate": None}
        for i in range(10):
            d = date(2026, 3, i + 1)
            key = f"daily/immigration-{d.isoformat()}.md.age"
            m["files"][key] = {"domain": "immigration", "tier": "daily", "date": d.isoformat(), "sha256": "x"}
        vault._save_manifest(m)
        vault._prune_backups("immigration", "daily", 7)
        loaded = vault._load_manifest()
        assert len([k for k in loaded["files"] if "immigration" in k]) == 7

    def test_yearly_not_pruned_because_none_keep(self, mock_vault_env):
        tier_dir = vault.BACKUP_DIR / "yearly"
        tier_dir.mkdir(parents=True, exist_ok=True)
        for yr in range(2020, 2027):
            (tier_dir / f"immigration-{yr}-12-31.md.age").write_bytes(b"x")
        # Pruning with keep_n=None must be a no-op (caller guards this)
        # Direct call with keep_n=None would fail — verify caller (GFS_RETENTION) sends None
        assert vault.GFS_RETENTION["yearly"] is None


# ---------------------------------------------------------------------------
# GFS Vault Backup — do_encrypt triggers snapshot
# ---------------------------------------------------------------------------

class TestEncryptTriggersBackup:
    """do_encrypt must call _backup_snapshot after successful encryption."""

    def test_encrypt_creates_backup(self, mock_vault_env, capsys):
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

        # Backup directory should have been created with at least one file
        backup_files = list(vault.BACKUP_DIR.rglob("*.md.age"))
        assert len(backup_files) >= 1

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
    """do_validate_backup \u2014 happy path and all failure modes."""

    def _seed_backup(self, mock_vault_env, domain="immigration",
                     tier="daily", d="2026-03-13", content=None):
        """Create a real backup file and manifest entry."""
        if content is None:
            content = "---\nschema_version: '1.0'\n---\n# Immigration\n" + "word " * 50
        tier_dir = vault.BACKUP_DIR / tier
        tier_dir.mkdir(parents=True, exist_ok=True)
        dest = tier_dir / f"{domain}-{d}.md.age"
        dest.write_bytes(content.encode())
        sha256 = vault._file_sha256(dest)
        m = vault._load_manifest()
        m["files"][f"{tier}/{domain}-{d}.md.age"] = {
            "sha256": sha256, "size": dest.stat().st_size,
            "domain": domain, "tier": tier, "date": d,
            "created": "2026-03-13T00:00:00+00:00",
        }
        vault._save_manifest(m)
        return dest

    def test_validate_ok_happy_path(self, mock_vault_env, capsys):
        self._seed_backup(mock_vault_env)

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
        # last_validate should be updated
        m = vault._load_manifest()
        assert m["last_validate"] is not None

    def test_validate_fails_checksum_mismatch(self, mock_vault_env, capsys):
        self._seed_backup(mock_vault_env)
        # Corrupt the backup file after manifest was written
        backup = vault.BACKUP_DIR / "daily" / "immigration-2026-03-13.md.age"
        backup.write_bytes(b"tampered content!!!")

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"):
            with pytest.raises(SystemExit):
                vault.do_validate_backup()

        out = capsys.readouterr().out
        assert "CHECKSUM" in out.upper()

    def test_validate_fails_missing_file(self, mock_vault_env, capsys):
        self._seed_backup(mock_vault_env)
        # Delete the backup file
        (vault.BACKUP_DIR / "daily" / "immigration-2026-03-13.md.age").unlink()

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"):
            with pytest.raises(SystemExit):
                vault.do_validate_backup()

        assert "MISSING" in capsys.readouterr().out

    def test_validate_fails_decrypt_error(self, mock_vault_env, capsys):
        self._seed_backup(mock_vault_env)

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", return_value=False):
            with pytest.raises(SystemExit):
                vault.do_validate_backup()

        assert "DECRYPT" in capsys.readouterr().out

    def test_validate_fails_empty_content(self, mock_vault_env, capsys):
        self._seed_backup(mock_vault_env)

        def empty_decrypt(key, infile, outfile):
            outfile.write_text("")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", side_effect=empty_decrypt):
            with pytest.raises(SystemExit):
                vault.do_validate_backup()

        assert "EMPTY" in capsys.readouterr().out

    def test_validate_fails_no_yaml_frontmatter(self, mock_vault_env, capsys):
        self._seed_backup(mock_vault_env)

        def no_yaml_decrypt(key, infile, outfile):
            outfile.write_text("NO FRONTMATTER HERE\n" + "word " * 50)
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", side_effect=no_yaml_decrypt):
            with pytest.raises(SystemExit):
                vault.do_validate_backup()

        assert "YAML" in capsys.readouterr().out

    def test_validate_fails_too_short(self, mock_vault_env, capsys):
        self._seed_backup(mock_vault_env)

        def short_decrypt(key, infile, outfile):
            outfile.write_text("---\ndata: ok\n---\nfew words only")
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", side_effect=short_decrypt):
            with pytest.raises(SystemExit):
                vault.do_validate_backup()

        assert "SHORT" in capsys.readouterr().out.upper()

    def test_validate_domain_filter(self, mock_vault_env, capsys):
        """--domain flag validates only matching entries."""
        self._seed_backup(mock_vault_env, domain="immigration")
        self._seed_backup(mock_vault_env, domain="finance", d="2026-03-13")

        validated = []

        def track_decrypt(key, infile, outfile):
            outfile.write_text("---\nschema_version: '1.0'\n---\n# Content\n" + "word " * 50)
            return True

        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("scripts.vault.age_decrypt", side_effect=track_decrypt) as mock_d:
            vault.do_validate_backup(domain="immigration")
            assert mock_d.call_count == 1

    def test_validate_no_backups_exits_gracefully(self, mock_vault_env, capsys):
        """When no backups exist, prints message and returns without error."""
        with patch("scripts.vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"):
            vault.do_validate_backup()  # should not raise

        out = capsys.readouterr().out
        assert "No backups" in out


# ---------------------------------------------------------------------------
# GFS Vault Backup — do_backup_status
# ---------------------------------------------------------------------------

class TestBackupStatus:
    def test_status_shows_never_when_no_backups(self, mock_vault_env, capsys):
        vault.do_backup_status()
        out = capsys.readouterr().out
        assert "NEVER" in out or "No backups" in out

    def test_status_shows_tiers(self, mock_vault_env, capsys):
        # Seed one backup per tier
        for tier, d in [("daily", "2026-03-13"), ("weekly", "2026-03-08"),
                        ("monthly", "2026-02-28"), ("yearly", "2025-12-31")]:
            (vault.BACKUP_DIR / tier).mkdir(parents=True, exist_ok=True)
            f = vault.BACKUP_DIR / tier / f"immigration-{d}.md.age"
            f.write_bytes(b"x" * 100)
            m = vault._load_manifest()
            m["files"][f"{tier}/immigration-{d}.md.age"] = {
                "sha256": vault._file_sha256(f), "size": 100,
                "domain": "immigration", "tier": tier, "date": d,
                "created": "2026-03-13T00:00:00+00:00",
            }
            vault._save_manifest(m)

        vault.do_backup_status()
        out = capsys.readouterr().out
        assert "DAILY" in out
        assert "WEEKLY" in out
        assert "MONTHLY" in out
        assert "YEARLY" in out

    def test_status_warns_when_validation_overdue(self, mock_vault_env, capsys):
        # Set last_validate to 40 days ago
        from datetime import datetime, timedelta, timezone
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
        m = vault._load_manifest()
        m["last_validate"] = old_ts
        vault._save_manifest(m)

        vault.do_backup_status()
        out = capsys.readouterr().out
        assert "overdue" in out.lower() or "⚠" in out
