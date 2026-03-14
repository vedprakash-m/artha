import pytest
import os
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import vault logic
import scripts.vault as vault

@pytest.fixture
def mock_vault_env(temp_artha_dir, monkeypatch):
    """Set up a mock environment for vault.py."""
    monkeypatch.setattr(vault, "ARTHA_DIR", temp_artha_dir)
    monkeypatch.setattr(vault, "STATE_DIR", temp_artha_dir / "state")
    monkeypatch.setattr(vault, "CONFIG_DIR", temp_artha_dir / "config")
    monkeypatch.setattr(vault, "LOCK_FILE", temp_artha_dir / ".artha-decrypted")
    monkeypatch.setattr(vault, "AUDIT_LOG", temp_artha_dir / "state" / "audit.md")
    
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
