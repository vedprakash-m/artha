"""Phase 0 vault tests — YAML file encryption/decryption support.

Verifies that vault.py correctly handles .yaml sensitive files (gallery.yaml,
gallery_memory.yaml) alongside the existing .md files without regression.

Spec: §15.6
"""
import yaml
import pytest
from pathlib import Path
from unittest.mock import patch

import vault
import foundation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_age_content(label: str = "test") -> str:
    """Return content that passes vault._is_valid_age_file (age header + padding)."""
    return "age-encryption.org/v1\n-> X25519 fake\n" + "A" * 80 + f"\n--- {label}"


def _fake_encrypt(pubkey, infile, outfile):
    """Mock age_encrypt — passes post-encrypt size verification."""
    size = infile.stat().st_size
    base = _fake_age_content("enc")
    if len(base) < size:
        base += "P" * (size - len(base))
    outfile.write_text(base)
    return True


def _fake_decrypt(privkey, infile, outfile):
    """Mock age_decrypt — restores content written to the .age file during fake encrypt.

    In tests, we store the original YAML content directly in the .age file since
    we bypass real encryption. The decrypt mock reads and writes it through.
    """
    content = infile.read_text()
    # Strip the fake age header to get back to YAML
    lines = content.split("\n")
    # If it starts with age header, reconstruct original content
    # For YAML round-trip we store original alongside the fake header
    outfile.write_text(content)
    return True


@pytest.fixture
def mock_vault_env(temp_artha_dir, monkeypatch):
    """Patch vault module aliases and foundation._config for isolated testing."""
    state_dir = temp_artha_dir / "state"
    config_dir = temp_artha_dir / "config"
    backup_dir = temp_artha_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(vault, "ARTHA_DIR",  temp_artha_dir)
    monkeypatch.setattr(vault, "STATE_DIR",  state_dir)
    monkeypatch.setattr(vault, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(vault, "LOCK_FILE",  temp_artha_dir / ".artha-decrypted")
    monkeypatch.setattr(vault, "AUDIT_LOG",  state_dir / "audit.md")

    monkeypatch.setitem(foundation._config, "ARTHA_DIR",       temp_artha_dir)
    monkeypatch.setitem(foundation._config, "STATE_DIR",       state_dir)
    monkeypatch.setitem(foundation._config, "CONFIG_DIR",      config_dir)
    monkeypatch.setitem(foundation._config, "LOCK_FILE",       temp_artha_dir / ".artha-decrypted")
    monkeypatch.setitem(foundation._config, "AUDIT_LOG",       state_dir / "audit.md")
    monkeypatch.setitem(foundation._config, "BACKUP_DIR",      backup_dir)
    monkeypatch.setitem(foundation._config, "BACKUP_MANIFEST", backup_dir / "manifest.json")

    # Minimal SENSITIVE_FILES with 1 .md + 1 .yaml for focused testing
    monkeypatch.setitem(foundation._config, "SENSITIVE_FILES", [
        ("health", ".md"),
        ("gallery", ".yaml"),
    ])

    config_dir.mkdir(parents=True, exist_ok=True)
    return temp_artha_dir


# ---------------------------------------------------------------------------
# §15.6 Tests
# ---------------------------------------------------------------------------

def test_sensitive_files_tuple_format():
    """SENSITIVE_FILES entries are (domain, extension) tuples, not plain strings."""
    from foundation import _config, _normalize_sensitive_files
    entries = _normalize_sensitive_files(_config["SENSITIVE_FILES"])
    for entry in entries:
        assert isinstance(entry, tuple), f"Expected tuple, got {type(entry)}: {entry!r}"
        assert len(entry) == 2, f"Expected (domain, ext) 2-tuple, got {entry!r}"
        domain, ext = entry
        assert isinstance(domain, str) and domain, "domain must be non-empty str"
        assert isinstance(ext, str) and ext.startswith("."), f"ext must start with '.': {ext!r}"


def test_vault_roundtrip_yaml_file(mock_vault_env, monkeypatch):
    """Encrypt then decrypt a .yaml file — content round-trips losslessly."""
    state_dir = mock_vault_env / "state"
    gallery_yaml = state_dir / "gallery.yaml"

    original_content = "schema_version: '1.0'\ncards: []\n"
    gallery_yaml.write_text(original_content)

    # Create lock file (active session)
    lock = mock_vault_env / ".artha-decrypted"
    lock.touch()

    with patch("vault.get_public_key", return_value="age1testpubkey"), \
         patch("vault.age_encrypt", side_effect=_fake_encrypt), \
         patch("vault.check_age_installed", return_value=True), \
         patch("vault.is_integrity_safe", return_value=True), \
         patch("backup.backup_snapshot", return_value=1), \
         patch("backup.load_backup_registry", return_value={}):
        vault.do_encrypt()

    age_file = state_dir / "gallery.yaml.age"
    assert age_file.exists(), "gallery.yaml.age should exist after encrypt"
    assert not gallery_yaml.exists(), "gallery.yaml should be removed after encrypt"

    # Now decrypt — vault is encrypted (no lock file), call decrypt directly
    def _restore_decrypt(privkey, infile, outfile):
        outfile.write_text(original_content)
        return True

    with patch("vault.get_private_key", return_value="AGE-SECRET-KEY-test"), \
         patch("vault.age_decrypt", side_effect=_restore_decrypt), \
         patch("vault.check_age_installed", return_value=True):
        vault.do_decrypt()

    assert gallery_yaml.exists(), "gallery.yaml should be restored after decrypt"
    assert gallery_yaml.read_text() == original_content, "Round-trip content must match"


def test_vault_yaml_age_created(mock_vault_env, monkeypatch):
    """gallery.yaml.age is created and gallery.yaml removed after encrypt."""
    state_dir = mock_vault_env / "state"
    (state_dir / "gallery.yaml").write_text("schema_version: '1.0'\ncards: []\n")
    (mock_vault_env / ".artha-decrypted").touch()

    with patch("vault.get_public_key", return_value="age1testpubkey"), \
         patch("vault.age_encrypt", side_effect=_fake_encrypt), \
         patch("vault.check_age_installed", return_value=True), \
         patch("vault.is_integrity_safe", return_value=True), \
         patch("backup.backup_snapshot", return_value=1), \
         patch("backup.load_backup_registry", return_value={}):
        vault.do_encrypt()

    assert (state_dir / "gallery.yaml.age").exists()
    assert not (state_dir / "gallery.yaml").exists()


def test_vault_decrypt_restores_yaml(mock_vault_env, monkeypatch):
    """Decrypted gallery.yaml is valid YAML parseable by yaml.safe_load()."""
    state_dir = mock_vault_env / "state"
    original = {"schema_version": "1.0", "cards": []}
    original_text = "schema_version: '1.0'\ncards: []\n"

    age_file = state_dir / "gallery.yaml.age"
    age_file.write_text(_fake_age_content())
    # No lock file — vault is in encrypted state, decrypt is allowed

    def _restore(privkey, infile, outfile):
        outfile.write_text(original_text)
        return True

    with patch("vault.get_private_key", return_value="AGE-SECRET-KEY-test"), \
         patch("vault.age_decrypt", side_effect=_restore), \
         patch("vault.check_age_installed", return_value=True):
        vault.do_decrypt()

    restored = state_dir / "gallery.yaml"
    assert restored.exists()
    parsed = yaml.safe_load(restored.read_text())
    assert parsed == original, f"Parsed YAML must match original: {parsed!r}"


def test_vault_md_files_unaffected(mock_vault_env, monkeypatch):
    """Existing .md sensitive files still encrypt/decrypt correctly after refactoring."""
    state_dir = mock_vault_env / "state"
    health_md = state_dir / "health.md"
    health_md.write_text("---\ntitle: health\n---\nBlood pressure normal.\n")
    (mock_vault_env / ".artha-decrypted").touch()

    with patch("vault.get_public_key", return_value="age1testpubkey"), \
         patch("vault.age_encrypt", side_effect=_fake_encrypt), \
         patch("vault.check_age_installed", return_value=True), \
         patch("vault.is_integrity_safe", return_value=True), \
         patch("backup.backup_snapshot", return_value=1), \
         patch("backup.load_backup_registry", return_value={}):
        vault.do_encrypt()

    restored_age = state_dir / "health.md.age"
    assert restored_age.exists(), "health.md.age should exist"
    assert not health_md.exists(), "health.md should be removed after encrypt"


def test_vault_integrity_check_yaml(mock_vault_env, monkeypatch):
    """Integrity checker accepts .yaml files with no prior .age baseline (new file path)."""
    state_dir = mock_vault_env / "state"
    yaml_file = state_dir / "gallery.yaml"
    yaml_file.write_text("schema_version: '1.0'\ncards: []\n")
    age_file  = state_dir / "gallery.yaml.age"
    # No .age file yet — is_integrity_safe should return True (new file, no baseline)

    result = vault.is_integrity_safe(yaml_file, age_file)
    assert result is True, (
        "is_integrity_safe must return True for a new .yaml file with no .age baseline"
    )


def test_gitignore_covers_yaml_age():
    """.gitignore contains state/*.yaml.age pattern (§15.6 gate check)."""
    gitignore = Path(__file__).parent.parent.parent / ".gitignore"
    assert gitignore.exists(), ".gitignore must exist"
    content = gitignore.read_text()
    assert "state/*.yaml.age" in content, \
        "'.gitignore' must contain 'state/*.yaml.age' to prevent accidental commit of YAML age files"
