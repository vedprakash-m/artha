import json
import pytest
import os
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import vault logic
import vault as vault
import foundation as foundation


def _fake_age_content(label: str = "test") -> str:
    """Return content that passes vault._is_valid_age_file (age header + padding).

    Real age files start with 'age-encryption.org' and are > 100 bytes.
    This creates a test stub that passes pre-validation so tests can exercise
    the mocked age_decrypt path.
    """
    return "age-encryption.org/v1\n-> X25519 fake\n" + "A" * 80 + f"\n--- {label}"


def _fake_encrypt(pubkey, infile, outfile):
    """Mock age_encrypt that passes post-encrypt size verification (#8).

    Produces output with a valid age header (passes is_valid_age_file) AND
    pads to at least the input file size (passes post-encrypt truncation check).
    """
    size = infile.stat().st_size
    base = _fake_age_content("enc")
    if len(base) < size:
        base += "P" * (size - len(base))
    outfile.write_text(base)
    return True

@pytest.fixture
def mock_vault_env(temp_artha_dir, monkeypatch):
    """Set up a mock environment for vault.py (and backup.py via foundation._config).

    vault.py functions use frozen module-level aliases (STATE_DIR, LOCK_FILE, …)
    imported from foundation at module load time.  Those must be patched directly
    on the vault namespace.  backup.py and _mark_backup_failure() use _config[…]
    at call time, so the foundation._config dict must also be patched.
    """
    backup_dir = temp_artha_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    state_dir  = temp_artha_dir / "state"
    config_dir = temp_artha_dir / "config"

    # --- vault module aliases: used directly by vault.py function bodies ---
    monkeypatch.setattr(vault, "ARTHA_DIR",  temp_artha_dir)
    monkeypatch.setattr(vault, "STATE_DIR",  state_dir)
    monkeypatch.setattr(vault, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(vault, "LOCK_FILE",  temp_artha_dir / ".artha-decrypted")
    monkeypatch.setattr(vault, "AUDIT_LOG",  state_dir / "audit.md")

    # --- foundation._config: used by backup.py (lazy-imported) and _config[…] ---
    monkeypatch.setitem(foundation._config, "ARTHA_DIR",       temp_artha_dir)
    monkeypatch.setitem(foundation._config, "STATE_DIR",       state_dir)
    monkeypatch.setitem(foundation._config, "CONFIG_DIR",      config_dir)
    monkeypatch.setitem(foundation._config, "LOCK_FILE",       temp_artha_dir / ".artha-decrypted")
    monkeypatch.setitem(foundation._config, "AUDIT_LOG",       state_dir / "audit.md")
    monkeypatch.setitem(foundation._config, "BACKUP_DIR",      backup_dir)
    monkeypatch.setitem(foundation._config, "BACKUP_MANIFEST", backup_dir / "manifest.json")

    config_dir.mkdir(parents=True, exist_ok=True)

    return temp_artha_dir

def test_vault_status_inactive(mock_vault_env, capsys):
    """Verify status report when vault is inactive (encrypted)."""
    mock_run = MagicMock(stdout="age v1.1.1", stderr="", returncode=0)
    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("subprocess.run", return_value=mock_run):
        vault.do_status()
        captured = capsys.readouterr()
        assert "SESSION: INACTIVE" in captured.out
        assert "[MISSING]   immigration" in captured.out

def test_vault_health_ok(mock_vault_env, capsys):
    """Verify health check reports OK when hard capabilities are intact.

    The mock env has no GFS backups and no key export — these are soft
    warnings (exit 2), not hard failures.  Hard capabilities (age, key,
    state dir) all pass, so exit code must be 0 or 2 (not 1).
    """
    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="AGE-SECRET-KEY-1MOCK"), \
         patch("vault.get_public_key", return_value="age1mockpublickey"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="age v1.1.1", returncode=0)
        
        with pytest.raises(SystemExit) as exc:
            vault.do_health()
        # Exit 2 = hard capabilities OK, only soft warnings present
        assert exc.value.code in (0, 2)
        captured = capsys.readouterr()
        assert "vault.py health: OK" in captured.out


def test_vault_health_bak_files_exit_2_not_1(mock_vault_env, capsys):
    """Orphaned .bak files must produce exit 2 (soft warning), never exit 1 (hard failure).

    Before the 3-exit-code model, .bak files set ok=False → exit 1, which
    blocked catch-up entirely.  Regression test that confirms this path is gone.
    """
    # Create an orphaned .bak file in the state directory
    bak_file = mock_vault_env / "state" / "immigration.md.bak"
    bak_file.write_text("orphaned backup content")

    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="AGE-SECRET-KEY-1MOCK"), \
         patch("vault.get_public_key", return_value="age1mockpublickey"), \
         patch("subprocess.run", return_value=MagicMock(stdout="age v1.1.1", returncode=0)):
        with pytest.raises(SystemExit) as exc:
            vault.do_health()
    # Must be 2 (soft warn) — NEVER 1 (hard fail)
    assert exc.value.code == 2, f"Expected exit 2 for .bak files, got {exc.value.code}"
    captured = capsys.readouterr()
    assert "⚠" in captured.out
    assert "vault.py health: OK (warnings present)" in captured.out

def test_vault_decrypt_flow(mock_vault_env, capsys):
    """Verify the decryption flow (mocking age calls)."""
    # Create mock .age files with valid headers so pre-validation passes
    age_file = mock_vault_env / "state" / "immigration.md.age"
    age_file.write_text(_fake_age_content("immigration"))
    
    # Create contacts age file (contacts lives in state/ alongside other sensitive files)
    contacts_age = mock_vault_env / "state" / "contacts.md.age"
    contacts_age.write_text(_fake_age_content("contacts"))
    
    def side_effect_decrypt(key, infile, outfile):
        outfile.write_text("---\nschema_version: 1.0\n---\n# Decrypted Data")
        return True

    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("vault.age_decrypt", side_effect=side_effect_decrypt) as mock_decrypt:
        
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
    (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

    with patch("vault.check_age_installed", return_value=True), \
         patch("vault.get_public_key", return_value="age1mock"), \
         patch("vault.age_encrypt", side_effect=_fake_encrypt) as mock_encrypt:
        
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

    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("vault.age_decrypt", side_effect=side_effect_decrypt):
        
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
    
    with patch("vault.check_age_installed", return_value=True), \
         patch("vault.get_public_key", return_value="age1mock"), \
         patch("vault.age_encrypt", return_value=True):
        
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
    age_file.write_text(_fake_age_content("immigration"))
    # Prior plaintext exists (becomes the backup)
    plain_file.write_text("---\nprevious: good data\n---\n# Prior Content")

    def empty_decrypt(key, infile, outfile):
        outfile.write_text("")  # empty output — simulates corrupt decrypt
        return True

    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("vault.age_decrypt", side_effect=empty_decrypt):
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
    age_file.write_text(_fake_age_content("immigration"))
    plain_file.write_text("---\nprevious: good data\n---\n# Prior Content")

    def bad_yaml_decrypt(key, infile, outfile):
        outfile.write_text("NO YAML FRONTMATTER HERE\nsome garbage")
        return True

    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("vault.age_decrypt", side_effect=bad_yaml_decrypt):
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
    age_file.write_text(_fake_age_content("immigration"))
    plain_file.write_text("---\nprevious: good data\n---\n# Prior Content")

    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("vault.age_decrypt", return_value=False):
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
    age_file.write_text(_fake_age_content("immigration"))
    # No plain_file — first decrypt ever, no .bak will be created

    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("vault.age_decrypt", return_value=False):
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
    age_file.write_text(_fake_age_content("immigration"))
    plain_file.write_text("---\ncurrent: real data\n---\n# Current")
    # Simulate a stale partial .bak.tmp from a prior crashed session
    bak_tmp.write_text("PARTIAL GARBAGE")

    def good_decrypt(key, infile, outfile):
        outfile.write_text("---\nnew: data\n---\n# New")
        return True

    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("vault.age_decrypt", side_effect=good_decrypt):
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

    with patch("vault.check_age_installed", return_value=True), \
         patch("vault.get_public_key", return_value="age1mock"), \
         patch("vault.age_encrypt", side_effect=_fake_encrypt):
        vault.do_encrypt()

    assert not bak_file.exists()
    assert not plain_file.exists()
    assert age_file.exists()


def test_restore_bak_rejects_corrupt_backup(mock_vault_env, capsys):
    """_restore_bak must reject an empty or invalid .bak and log INTEGRITY_RESTORE_FAILED."""
    age_file = mock_vault_env / "state" / "immigration.md.age"
    plain_file = mock_vault_env / "state" / "immigration.md"
    bak_file = mock_vault_env / "state" / "immigration.md.bak"
    age_file.write_text(_fake_age_content("immigration"))
    # Pre-seed a corrupt (empty) .bak and no current plain_file
    bak_file.write_text("")  # corrupt/empty backup

    def bad_decrypt(key, infile, outfile):
        outfile.write_text("")  # empty
        return True

    with patch("vault.check_age_installed", return_value=True), \
         patch("keyring.get_password", return_value="mock-key"), \
         patch("vault.age_decrypt", side_effect=bad_decrypt):
        with pytest.raises(SystemExit):
            vault.do_decrypt()

    captured = capsys.readouterr()
    # Should warn that backup is empty, not silently restore garbage
    assert "empty" in captured.err.lower() or "backup" in captured.err.lower()
    # plain_file must NOT exist (we did not restore the corrupt backup)
    assert not plain_file.exists()


# ---------------------------------------------------------------------------
# GFS Vault Backup — do_encrypt triggers snapshot (integration via backup.py)
# ---------------------------------------------------------------------------

class TestEncryptTriggersBackup:
    """do_encrypt must call backup_snapshot (in backup.py) after successful encryption."""

    def test_encrypt_creates_backup_zip(self, mock_vault_env, capsys):
        plain = mock_vault_env / "state" / "immigration.md"
        age   = mock_vault_env / "state" / "immigration.md.age"
        plain.write_text("---\ndata: ok\n---\n" + "x " * 300)
        age.write_text("x" * 500)
        (mock_vault_env / ".artha-decrypted").touch()
        (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

        with patch("vault.check_age_installed", return_value=True), \
             patch("vault.get_public_key", return_value="age1mock"), \
             patch("vault.age_encrypt", side_effect=_fake_encrypt):
            vault.do_encrypt()

        zip_files = list(foundation._config["BACKUP_DIR"].rglob("*.zip"))
        assert len(zip_files) >= 1

    def test_encrypt_backup_not_called_on_error(self, mock_vault_env, capsys):
        """If encryption fails, backup must not be taken."""
        plain = mock_vault_env / "state" / "immigration.md"
        age   = mock_vault_env / "state" / "immigration.md.age"
        plain.write_text("---\ndata: ok\n---\n" + "x " * 300)
        age.write_text("x" * 500)
        (mock_vault_env / ".artha-decrypted").touch()
        (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

        with patch("vault.check_age_installed", return_value=True), \
             patch("vault.get_public_key", return_value="age1mock"), \
             patch("vault.age_encrypt", return_value=False), \
             patch("backup.backup_snapshot") as mock_snapshot, \
             patch("backup.load_backup_registry", return_value=[]):
            with pytest.raises(SystemExit):
                vault.do_encrypt()
            mock_snapshot.assert_not_called()


# ---------------------------------------------------------------------------
# Structural guards — vault.py must not contain extracted backup symbols
# ---------------------------------------------------------------------------

def test_vault_exports_crypto_primitives():
    """vault.py must re-export the foundation crypto API for backward compat."""
    assert hasattr(vault, "age_encrypt")
    assert hasattr(vault, "age_decrypt")
    assert hasattr(vault, "get_private_key")
    assert hasattr(vault, "get_public_key")


def test_vault_has_no_backup_functions():
    """Backup functions must NOT exist in vault module (extracted to backup.py)."""
    backup_fns = [
        "_backup_snapshot", "_load_backup_registry", "_file_sha256",
        "_load_manifest", "_save_manifest", "_get_backup_tier",
        "_prune_backups", "_zip_archive_path", "_select_backup_zip",
        "_restore_from_zip", "do_validate_backup", "do_backup_status",
        "do_restore", "do_install",
    ]
    for fn_name in backup_fns:
        assert not hasattr(vault, fn_name), f"vault should not have {fn_name}()"


# ---------------------------------------------------------------------------
# Help / usage output
# ---------------------------------------------------------------------------

class TestHelpAndUsage:
    def test_help_flag_exits_zero(self, monkeypatch):
        """--help exits with code 0 (not 1)."""
        monkeypatch.setattr("sys.argv", ["vault.py", "--help"])
        with pytest.raises(SystemExit) as exc:
            vault.main()
        assert exc.value.code == 0

    def test_dash_h_exits_zero(self, monkeypatch):
        """-h is an alias for --help."""
        monkeypatch.setattr("sys.argv", ["vault.py", "-h"])
        with pytest.raises(SystemExit) as exc:
            vault.main()
        assert exc.value.code == 0

    def test_help_flag_prints_commands(self, monkeypatch, capsys):
        """--help output includes the main command names."""
        monkeypatch.setattr("sys.argv", ["vault.py", "--help"])
        with pytest.raises(SystemExit):
            vault.main()
        out = capsys.readouterr().out
        for cmd in ("decrypt", "encrypt", "status", "health", "store-key"):
            assert cmd in out, f"Expected '{cmd}' in --help output"

    def test_no_args_exits_nonzero(self, monkeypatch):
        """Running with no arguments exits with code 1."""
        monkeypatch.setattr("sys.argv", ["vault.py"])
        with pytest.raises(SystemExit) as exc:
            vault.main()
        assert exc.value.code != 0

    def test_unknown_command_exits_nonzero(self, monkeypatch):
        """Unknown command exits with code 1."""
        monkeypatch.setattr("sys.argv", ["vault.py", "frobnicate"])
        with pytest.raises(SystemExit) as exc:
            vault.main()
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# Corrupt .age file pre-validation and quarantine
# ---------------------------------------------------------------------------

class TestAgeFilePreValidation:
    """Tests for the _is_valid_age_file check and quarantine flow in do_decrypt."""

    def test_is_valid_age_file_rejects_stub(self, tmp_path):
        """A 7-byte stub like the real-world immigration.md.age is rejected."""
        stub = tmp_path / "test.age"
        stub.write_text("enc-imm")
        assert not vault._is_valid_age_file(stub)

    def test_is_valid_age_file_rejects_small_file(self, tmp_path):
        """Files under _AGE_MIN_FILE_SIZE are rejected."""
        small = tmp_path / "test.age"
        small.write_bytes(b"age-encryption.org/v1\n")
        assert not vault._is_valid_age_file(small)

    def test_is_valid_age_file_rejects_wrong_header(self, tmp_path):
        """Files with wrong header are rejected even if large enough."""
        wrong = tmp_path / "test.age"
        wrong.write_bytes(b"NOT-AGE-FILE" + b"x" * 200)
        assert not vault._is_valid_age_file(wrong)

    def test_is_valid_age_file_accepts_valid(self, tmp_path):
        """Files with correct header and sufficient size pass."""
        valid = tmp_path / "test.age"
        valid.write_text(_fake_age_content("test"))
        assert vault._is_valid_age_file(valid)

    def test_is_valid_age_file_handles_missing(self, tmp_path):
        """Non-existent file returns False."""
        assert not vault._is_valid_age_file(tmp_path / "nonexistent.age")

    def test_decrypt_quarantines_corrupt_age_file(self, mock_vault_env, capsys):
        """A corrupt .age file is moved to .quarantine/ and decrypt continues."""
        # Create a corrupt stub (simulates the real-world immigration.md.age bug)
        corrupt = mock_vault_env / "state" / "immigration.md.age"
        corrupt.write_text("enc-imm")

        def good_decrypt(key, infile, outfile):
            outfile.write_text("---\nschema_version: 1.0\n---\n# Data")
            return True

        with patch("vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("vault.age_decrypt", side_effect=good_decrypt):
            vault.do_decrypt()

        # Corrupt file should be quarantined
        quarantine = mock_vault_env / "state" / ".quarantine"
        assert quarantine.exists()
        quarantined = list(quarantine.iterdir())
        assert len(quarantined) == 1
        assert "immigration.md.age" in quarantined[0].name
        # Original should be gone
        assert not corrupt.exists()
        # Lock file created — decrypt succeeded for healthy files
        assert (mock_vault_env / ".artha-decrypted").exists()
        out = capsys.readouterr()
        assert "quarantine" in out.out.lower() or "quarantine" in out.err.lower()

    def test_decrypt_continues_after_quarantine(self, mock_vault_env, capsys):
        """Valid files are still decrypted even when one file is quarantined."""
        # One corrupt, one valid
        (mock_vault_env / "state" / "immigration.md.age").write_text("stub")
        (mock_vault_env / "state" / "finance.md.age").write_text(_fake_age_content("finance"))

        def good_decrypt(key, infile, outfile):
            outfile.write_text("---\nschema_version: 1.0\n---\n# Finance Data\n" + "word " * 50)
            return True

        with patch("vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="mock-key"), \
             patch("vault.age_decrypt", side_effect=good_decrypt):
            vault.do_decrypt()

        # finance.md should exist (decrypted successfully)
        assert (mock_vault_env / "state" / "finance.md").exists()
        # immigration should be quarantined
        assert not (mock_vault_env / "state" / "immigration.md.age").exists()


# ---------------------------------------------------------------------------
# Encrypt safety net — missing public key
# ---------------------------------------------------------------------------

class TestEncryptSafetyNet:
    """Tests for the encrypt safety net when public key is missing."""

    def test_encrypt_missing_pubkey_does_not_die(self, mock_vault_env, capsys):
        """If public key is missing, encrypt exits 1 but with a clear message, not die()."""
        (mock_vault_env / "state" / "immigration.md").write_text("sensitive data")
        (mock_vault_env / ".artha-decrypted").touch()

        with patch("vault.check_age_installed", return_value=True), \
             patch("vault.get_public_key", side_effect=SystemExit(1)):
            with pytest.raises(SystemExit) as exc:
                vault.do_encrypt()
            assert exc.value.code == 1

        out = capsys.readouterr()
        assert "age_recipient" in out.err
        # Lock file should NOT be removed — plaintext still on disk
        assert (mock_vault_env / ".artha-decrypted").exists()
        # Plaintext should still exist — not silently deleted
        assert (mock_vault_env / "state" / "immigration.md").exists()

    def test_encrypt_orphan_cleanup(self, mock_vault_env, capsys):
        """After successful encrypt, orphaned plaintext alongside valid .age is cleaned."""
        # Simulate an orphaned plaintext stub alongside a valid .age
        (mock_vault_env / "state" / "immigration.md").write_text("---\ndata: ok\n---\n" + "x " * 300)
        (mock_vault_env / "state" / "immigration.md.age").write_text(_fake_age_content("imm"))
        (mock_vault_env / ".artha-decrypted").touch()
        (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

        with patch("vault.check_age_installed", return_value=True), \
             patch("vault.get_public_key", return_value="age1mock"), \
             patch("vault.age_encrypt", side_effect=_fake_encrypt):
            vault.do_encrypt()

        # After successful encrypt, orphaned plaintext should be cleaned
        # (the regular encrypt loop would handle immigration.md, and the
        # orphan cleanup handles any remaining stubs)
        assert not (mock_vault_env / "state" / "immigration.md").exists()


# ---------------------------------------------------------------------------
# Partial validation success — backup.py
# ---------------------------------------------------------------------------

class TestPartialValidation:
    """Tests that validate-backup updates last_validate on partial success."""

    @staticmethod
    def _make_zip(backup_dir, tier, date_str, files_dict):
        """Create a valid backup ZIP for tests."""
        import hashlib as _hl, json as _js, zipfile as _zf
        tier_dir = backup_dir / tier
        tier_dir.mkdir(parents=True, exist_ok=True)
        zip_path = tier_dir / f"{date_str}.zip"
        internal_files = {}
        with _zf.ZipFile(str(zip_path), "w", compression=_zf.ZIP_DEFLATED) as zf:
            for arc_path, (content, source_type, restore_path, name) in files_dict.items():
                sha256 = _hl.sha256(content).hexdigest()
                zf.writestr(arc_path, content)
                internal_files[arc_path] = {
                    "name": name, "sha256": sha256, "size": len(content),
                    "source_type": source_type, "restore_path": restore_path,
                }
            zf.writestr("manifest.json", _js.dumps({
                "artha_backup_version": "2", "created": f"{date_str}T00:00:00+00:00",
                "date": date_str, "tier": tier, "files": internal_files,
            }))
        return zip_path

    def _seed_multi_file_zip(self, backup_dir):
        """Create a ZIP with two files and register in manifest."""
        import backup as backup_mod
        files = {
            "state/immigration.md.age": (b"age-content-1", "state_encrypted", "state/immigration.md.age", "immigration"),
            "state/finance.md.age":     (b"age-content-2", "state_encrypted", "state/finance.md.age", "finance"),
        }
        zip_path = self._make_zip(backup_dir, "daily", "2026-03-14", files)
        sha = backup_mod._file_sha256(zip_path)
        m = backup_mod._load_manifest()
        m["snapshots"]["daily/2026-03-14.zip"] = {
            "created": "2026-03-14T00:00:00+00:00", "date": "2026-03-14", "tier": "daily",
            "sha256": sha, "size": zip_path.stat().st_size, "file_count": 2,
        }
        backup_mod._save_manifest(m)
        return zip_path

    def test_partial_success_updates_last_validate(self, mock_vault_env, capsys):
        """When 1/2 files validates, last_validate is still updated."""
        import backup as backup_mod
        backup_dir = foundation._config["BACKUP_DIR"]
        self._seed_multi_file_zip(backup_dir)

        call_count = [0]
        def partial_decrypt(key, infile, outfile):
            call_count[0] += 1
            if call_count[0] == 1:
                return False  # first file fails
            outfile.write_text("---\nschema_version: '1.0'\n---\n# Finance\n" + "word " * 50)
            return True

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             patch("backup.age_decrypt", side_effect=partial_decrypt):
            backup_mod.do_validate_backup()

        m = backup_mod._load_manifest()
        assert m["last_validate"] is not None
        assert m.get("last_validate_errors") == 1
        out = capsys.readouterr().out
        assert "1 passed" in out
        assert "1 failed" in out

    def test_full_success_clears_error_count(self, mock_vault_env, capsys):
        """When all files validate, last_validate_errors is removed from manifest."""
        import backup as backup_mod
        backup_dir = foundation._config["BACKUP_DIR"]

        files = {"state/finance.md.age": (b"age-content", "state_encrypted", "state/finance.md.age", "finance")}
        zip_path = self._make_zip(backup_dir, "daily", "2026-03-14", files)
        sha = backup_mod._file_sha256(zip_path)
        m = backup_mod._load_manifest()
        m["snapshots"]["daily/2026-03-14.zip"] = {
            "created": "2026-03-14T00:00:00+00:00", "date": "2026-03-14", "tier": "daily",
            "sha256": sha, "size": zip_path.stat().st_size, "file_count": 1,
        }
        m["last_validate_errors"] = 3  # leftover from prior partial success
        backup_mod._save_manifest(m)

        def good_decrypt(key, infile, outfile):
            outfile.write_text("---\nschema_version: '1.0'\n---\n# Finance\n" + "word " * 50)
            return True

        with patch("backup.check_age_installed", return_value=True), \
             patch("backup.get_private_key", return_value="mock-key"), \
             patch("backup.age_decrypt", side_effect=good_decrypt):
            backup_mod.do_validate_backup()

        m = backup_mod._load_manifest()
        assert m["last_validate"] is not None
        assert "last_validate_errors" not in m

    def test_health_summary_returns_3_tuple(self, mock_vault_env):
        """get_health_summary returns (count, last_validate, errors)."""
        import backup as backup_mod
        result = backup_mod.get_health_summary()
        assert len(result) == 3
        assert result == (0, None, 0)


# ---------------------------------------------------------------------------
# Advisory lock (#10)
# ---------------------------------------------------------------------------

class TestAdvisoryLock:
    """Tests for the advisory file lock that prevents concurrent vault operations."""

    def test_acquire_and_release(self, mock_vault_env):
        """Lock can be acquired and released cleanly."""
        assert vault._acquire_op_lock()
        lock_path = foundation._config["ARTHA_DIR"] / ".artha-op-lock"
        assert lock_path.exists()
        vault._release_op_lock()
        # After release, can acquire again
        assert vault._acquire_op_lock()
        vault._release_op_lock()

    def test_double_acquire_fails(self, mock_vault_env):
        """Second acquire fails while first is held (simulated via fd)."""
        fcntl = pytest.importorskip("fcntl", reason="fcntl is Unix-only")
        assert vault._acquire_op_lock()
        # Open a second fd to the same file — should fail to flock
        lock_path = foundation._config["ARTHA_DIR"] / ".artha-op-lock"
        fd2 = open(lock_path, "w")
        try:
            fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd2.close()
            vault._release_op_lock()
            pytest.skip("flock non-blocking not enforced on this FS")
        except (IOError, OSError):
            fd2.close()
        vault._release_op_lock()

    def test_decorator_releases_on_success(self, mock_vault_env, capsys):
        """@_with_op_lock releases the lock after successful function execution."""
        @vault._with_op_lock
        def sample_func():
            return 42

        result = sample_func()
        assert result == 42
        assert vault._op_lock_fd is None  # released

    def test_decorator_releases_on_exception(self, mock_vault_env):
        """@_with_op_lock releases the lock even when the wrapped function raises."""
        @vault._with_op_lock
        def failing_func():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            failing_func()
        assert vault._op_lock_fd is None  # released


# ---------------------------------------------------------------------------
# Sync-fence check (#2)
# ---------------------------------------------------------------------------

class TestSyncFence:
    """Tests for cloud sync detection before decrypt."""

    def test_non_cloud_path_skips_fence(self, mock_vault_env):
        """Non-cloud-synced paths return True immediately (no sleep)."""
        # temp dir path doesn't contain any cloud markers
        assert vault._is_cloud_synced() is False
        assert vault._check_sync_fence() is True

    def test_cloud_markers_detected(self, mock_vault_env, monkeypatch):
        """Known cloud sync markers in ARTHA_DIR are detected."""
        monkeypatch.setitem(foundation._config, "ARTHA_DIR",
                            Path("/Users/test/Library/CloudStorage/OneDrive/Artha"))
        assert vault._is_cloud_synced() is True


# ---------------------------------------------------------------------------
# Auto-lock mtime guard (#4)
# ---------------------------------------------------------------------------

class TestAutoLockMtimeGuard:
    """Tests for the mtime guard that defers auto-lock during active writes."""

    def test_auto_lock_defers_on_recent_write(self, mock_vault_env, capsys):
        """Auto-lock is deferred if a state .md file was modified recently."""
        lock = mock_vault_env / ".artha-decrypted"
        lock.touch()
        # Set lock mtime to 31 min ago (past TTL)
        old = time.time() - (31 * 60)
        os.utime(lock, (old, old))
        # Create a recently-modified plaintext file
        md = mock_vault_env / "state" / "immigration.md"
        md.write_text("---\nactive data\n---\n")

        result = vault.do_auto_lock()
        assert result == 0
        out = capsys.readouterr().out
        assert "deferring" in out.lower()
        # Lock file mtime should be refreshed
        assert time.time() - os.path.getmtime(lock) < 5

    def test_auto_lock_proceeds_when_no_recent_writes(self, mock_vault_env, capsys):
        """Auto-lock proceeds if no state files were recently modified."""
        lock = mock_vault_env / ".artha-decrypted"
        lock.touch()
        old = time.time() - (31 * 60)
        os.utime(lock, (old, old))
        # Create a plaintext file modified >60s ago
        md = mock_vault_env / "state" / "immigration.md"
        md.write_text("---\nold data\n---\n")
        os.utime(md, (old, old))
        (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

        with patch("vault.check_age_installed", return_value=True), \
             patch("vault.get_public_key", return_value="age1mock"), \
             patch("vault.age_encrypt", side_effect=_fake_encrypt):
            result = vault.do_auto_lock()
        assert result == 0


# ---------------------------------------------------------------------------
# Net-negative override + pre-shrink pin (#5)
# ---------------------------------------------------------------------------

class TestNetNegativeOverride:
    """Tests for ARTHA_FORCE_SHRINK env var and .age.pre-shrink pinning."""

    def test_override_all_domains(self, mock_vault_env, capsys, monkeypatch):
        """ARTHA_FORCE_SHRINK=1 allows shrink for any domain."""
        plain = mock_vault_env / "state" / "immigration.md"
        age = mock_vault_env / "state" / "immigration.md.age"
        age.write_text("X" * 1000)
        plain.write_text("tiny")
        monkeypatch.setenv("ARTHA_FORCE_SHRINK", "1")

        result = vault.is_integrity_safe(plain, age)
        assert result is True
        # Old .age should be pinned
        pre_shrink = Path(str(age) + ".pre-shrink")
        assert pre_shrink.exists()
        assert pre_shrink.read_text() == "X" * 1000
        out = capsys.readouterr().out
        assert "Override accepted" in out

    def test_override_specific_domain(self, mock_vault_env, capsys, monkeypatch):
        """ARTHA_FORCE_SHRINK=immigration only overrides that domain."""
        plain_imm = mock_vault_env / "state" / "immigration.md"
        age_imm = mock_vault_env / "state" / "immigration.md.age"
        age_imm.write_text("X" * 1000)
        plain_imm.write_text("tiny")
        monkeypatch.setenv("ARTHA_FORCE_SHRINK", "immigration")
        assert vault.is_integrity_safe(plain_imm, age_imm) is True

        # Different domain should still be blocked
        plain_fin = mock_vault_env / "state" / "finance.md"
        age_fin = mock_vault_env / "state" / "finance.md.age"
        age_fin.write_text("Y" * 1000)
        plain_fin.write_text("small")
        assert vault.is_integrity_safe(plain_fin, age_fin) is False

    def test_no_override_still_blocks(self, mock_vault_env, capsys):
        """Without env var, shrink is still blocked."""
        plain = mock_vault_env / "state" / "immigration.md"
        age = mock_vault_env / "state" / "immigration.md.age"
        age.write_text("X" * 1000)
        plain.write_text("tiny")
        assert vault.is_integrity_safe(plain, age) is False
        out = capsys.readouterr().out
        assert "ARTHA_FORCE_SHRINK" in out


# ---------------------------------------------------------------------------
# Post-encrypt size verification (#8)
# ---------------------------------------------------------------------------

class TestPostEncryptVerification:
    """Tests for truncation detection after age_encrypt."""

    def test_truncated_encrypt_detected(self, mock_vault_env, capsys):
        """Encrypt output smaller than input triggers truncation error."""
        plain = mock_vault_env / "state" / "immigration.md"
        plain.write_text("---\ndata: ok\n---\n" + "x " * 500)  # ~1016 bytes
        (mock_vault_env / ".artha-decrypted").touch()
        (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

        def truncating_encrypt(pubkey, infile, outfile):
            outfile.write_text("tiny")  # 4 bytes < 1016 bytes
            return True

        with patch("vault.check_age_installed", return_value=True), \
             patch("vault.get_public_key", return_value="age1mock"), \
             patch("vault.age_encrypt", side_effect=truncating_encrypt):
            with pytest.raises(SystemExit):
                vault.do_encrypt()

        out = capsys.readouterr()
        assert "truncated" in out.err.lower()
        # .md should still exist (deferred cleanup didn't run because errors > 0)
        assert plain.exists()

    def test_valid_encrypt_passes_verification(self, mock_vault_env, capsys):
        """Encrypt output >= input passes verification."""
        plain = mock_vault_env / "state" / "immigration.md"
        plain.write_text("small")
        (mock_vault_env / ".artha-decrypted").touch()
        (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

        with patch("vault.check_age_installed", return_value=True), \
             patch("vault.get_public_key", return_value="age1mock"), \
             patch("vault.age_encrypt", side_effect=_fake_encrypt):
            vault.do_encrypt()

        assert not plain.exists()
        assert (mock_vault_env / "state" / "immigration.md.age").exists()


# ---------------------------------------------------------------------------
# Encrypt failure lockdown (#9)
# ---------------------------------------------------------------------------

class TestEncryptLockdown:
    """Tests for permission lockdown on encrypt failure."""

    def test_lockdown_on_encrypt_failure(self, mock_vault_env, capsys):
        """Failed encrypt triggers permission lockdown on remaining plaintext."""
        plain = mock_vault_env / "state" / "immigration.md"
        plain.write_text("---\ndata: sensitive\n---\n")
        (mock_vault_env / ".artha-decrypted").touch()
        (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

        with patch("vault.check_age_installed", return_value=True), \
             patch("vault.get_public_key", return_value="age1mock"), \
             patch("vault.age_encrypt", return_value=False):
            with pytest.raises(SystemExit):
                vault.do_encrypt()

        out = capsys.readouterr()
        assert "locked down" in out.out.lower() or "lockdown" in out.out.lower()

    def test_lockdown_on_missing_pubkey(self, mock_vault_env, capsys):
        """Missing public key triggers lockdown."""
        (mock_vault_env / "state" / "immigration.md").write_text("sensitive")
        (mock_vault_env / ".artha-decrypted").touch()

        with patch("vault.check_age_installed", return_value=True), \
             patch("vault.get_public_key", side_effect=SystemExit(1)):
            with pytest.raises(SystemExit):
                vault.do_encrypt()

        out = capsys.readouterr()
        # Lock file should NOT be removed
        assert (mock_vault_env / ".artha-decrypted").exists()

    def test_unlock_restores_permissions(self, mock_vault_env):
        """_unlock_plaintext restores permissions on locked-down files."""
        plain = mock_vault_env / "state" / "immigration.md"
        plain.write_text("locked data")
        os.chmod(plain, 0o000)

        vault._unlock_plaintext()

        # Should be readable again
        assert os.access(plain, os.R_OK)
        assert plain.read_text() == "locked data"


# ---------------------------------------------------------------------------
# Deferred plaintext deletion (#1)
# ---------------------------------------------------------------------------

class TestDeferredDeletion:
    """Tests for deferred .md deletion during encrypt."""

    def test_md_files_survive_partial_encrypt(self, mock_vault_env, capsys):
        """If one file fails to encrypt, ALL .md files survive (not just the failed one)."""
        (mock_vault_env / "state" / "immigration.md").write_text("---\ndata: ok\n---\n" + "x " * 300)
        (mock_vault_env / "state" / "contacts.md").write_text("---\ndata: ok\n---\n" + "y " * 300)
        (mock_vault_env / ".artha-decrypted").touch()
        (mock_vault_env / "state" / "audit.md").unlink(missing_ok=True)

        call_count = [0]
        def partial_encrypt(pubkey, infile, outfile):
            call_count[0] += 1
            if "immigration" in str(infile):
                return _fake_encrypt(pubkey, infile, outfile)
            return False  # contacts fails

        with patch("vault.check_age_installed", return_value=True), \
             patch("vault.get_public_key", return_value="age1mock"), \
             patch("vault.age_encrypt", side_effect=partial_encrypt):
            with pytest.raises(SystemExit):
                vault.do_encrypt()

        # Both .md files should still exist (deferred cleanup skipped on error)
        assert (mock_vault_env / "state" / "immigration.md").exists()
        assert (mock_vault_env / "state" / "contacts.md").exists()


# ---------------------------------------------------------------------------
# Health check key format validation (#3)
# ---------------------------------------------------------------------------

class TestHealthKeyValidation:
    """Tests for key format and export status checks in do_health."""

    def test_health_warns_on_invalid_key_format(self, mock_vault_env, capsys):
        """Health check fails if keyring key doesn't have AGE-SECRET-KEY- prefix."""
        with patch("vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="not-a-valid-key"), \
             patch("vault.get_public_key", return_value="age1mockpublickey"), \
             patch("subprocess.run", return_value=MagicMock(stdout="age v1.1.1", returncode=0)):
            with pytest.raises(SystemExit) as exc:
                vault.do_health()
            assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "INVALID FORMAT" in out

    def test_health_shows_key_export_status(self, mock_vault_env, capsys):
        """Health check shows key backup/export status.

        Mock env has no key export recorded, so a warning is expected and
        the exit code is 2 (soft warning, not hard failure).
        """
        with patch("vault.check_age_installed", return_value=True), \
             patch("keyring.get_password", return_value="AGE-SECRET-KEY-1MOCK"), \
             patch("vault.get_public_key", return_value="age1mockpublickey"), \
             patch("subprocess.run", return_value=MagicMock(stdout="age v1.1.1", returncode=0)):
            with pytest.raises(SystemExit) as exc:
                vault.do_health()
            # Hard capabilities OK; key-never-exported is a soft warning (exit 2)
            assert exc.value.code in (0, 2)
        out = capsys.readouterr().out
        assert "Key backup:" in out or "NEVER exported" in out


# ---------------------------------------------------------------------------
# DEBT-VAULT-001: vault_hook.py static fallback must include 'employment'
# ---------------------------------------------------------------------------

class TestVaultHookFallback:
    """DEBT-VAULT-001: Verify employment is in the static SENSITIVE_DOMAINS fallback."""

    def test_employment_in_fallback(self):
        """vault_hook.SENSITIVE_DOMAINS must contain 'employment'."""
        import importlib, sys
        # Force reimport so we don't get a cached version
        if "vault_hook" in sys.modules:
            del sys.modules["vault_hook"]
        import vault_hook
        assert "employment" in vault_hook.SENSITIVE_DOMAINS, (
            "DEBT-VAULT-001: 'employment' must be in vault_hook.SENSITIVE_DOMAINS fallback. "
            "Salary, RSU, and comp data must be treated as sensitive at the hook level."
        )

    def test_fallback_minimum_domains(self):
        """vault_hook.SENSITIVE_DOMAINS must include all 12 expected domains."""
        import sys
        if "vault_hook" in sys.modules:
            del sys.modules["vault_hook"]
        import vault_hook
        required = {
            "immigration", "finance", "insurance", "estate", "health",
            "audit", "vehicle", "contacts", "occasions", "transactions",
            "kids", "employment",
        }
        missing = required - set(vault_hook.SENSITIVE_DOMAINS)
        assert not missing, (
            f"vault_hook.SENSITIVE_DOMAINS missing domains: {sorted(missing)}. "
            f"These domains contain sensitive data and must trigger stray-file warnings."
        )
