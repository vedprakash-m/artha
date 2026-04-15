"""scripts/lib/state_reader.py — Domain-aware state file reader (DEBT-ARCH-001).

Provides a single entry-point for reading Artha domain state files, enforcing
the vault encryption policy from config/domain_registry.yaml.

Public API
----------
VaultPolicyViolation
    Raised when a caller attempts to read a vault-required domain's raw (unencrypted)
    state file directly, or when a requires_vault domain lacks a .md.age state_file.

StateReader
    Domain-aware reader.  Usage:

        reader = StateReader()
        text = reader.read("immigration")          # auto-decrypts .md.age
        text = reader.read_raw("state/goals.md")   # safe for non-vault domains

Architecture notes
------------------
- Phase 1 (this file): Registry lookup, policy enforcement, plaintext read for
  non-vault domains, and VaultPolicyViolation for vault domains if age-cli is absent.
- Phase 2 (post-DEBT-ARCH-001 → DEBT-VAULT-003 Phase 2): Auto-decrypt via
  scripts/vault.py subprocess when age-cli + credential store key are available.
- This file MUST import only stdlib so it can be used from pre-activation
  (bare hook) contexts where the venv is unavailable.

Ref: specs/debts.md DEBT-ARCH-001, DEBT-VAULT-003
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate repository root and registry
# ---------------------------------------------------------------------------

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_REGISTRY_PATH = _ARTHA_DIR / "config" / "domain_registry.yaml"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class VaultPolicyViolation(RuntimeError):
    """Raised when access to a vault-required state file would bypass encryption.

    This is a security invariant — all requires_vault: true domains must be
    read through StateReader.read() which auto-decrypts; direct open() on the
    plaintext path is forbidden.
    """


# ---------------------------------------------------------------------------
# Registry loader (cached per-process)
# ---------------------------------------------------------------------------

_REGISTRY_CACHE: dict | None = None


def _load_registry() -> dict:
    """Load config/domain_registry.yaml (cached)."""
    global _REGISTRY_CACHE  # noqa: PLW0603
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    try:
        import yaml  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "StateReader requires PyYAML. Install it with: pip install pyyaml"
        ) from exc
    if not _REGISTRY_PATH.exists():
        raise FileNotFoundError(
            f"domain_registry.yaml not found at {_REGISTRY_PATH}"
        )
    with _REGISTRY_PATH.open(encoding="utf-8") as fh:
        reg = yaml.safe_load(fh) or {}
    _REGISTRY_CACHE = reg
    return reg


def _domain_cfg(domain_name: str) -> dict:
    """Return the registry config dict for *domain_name*, or raise KeyError."""
    reg = _load_registry()
    domains = reg.get("domains") or {}
    if domain_name not in domains:
        raise KeyError(
            f"Domain '{domain_name}' not found in domain_registry.yaml. "
            f"Available domains: {sorted(domains.keys())}"
        )
    return domains[domain_name] or {}


def _vault_required_paths() -> frozenset[str]:
    """Return a frozenset of normalized absolute state-file paths that require vault."""
    reg = _load_registry()
    domains = reg.get("domains") or {}
    paths: set[str] = set()
    for cfg in domains.values():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("requires_vault"):
            sf = cfg.get("state_file", "")
            if sf:
                abs_path = str((_ARTHA_DIR / sf).resolve())
                paths.add(abs_path)
    return frozenset(paths)


# ---------------------------------------------------------------------------
# StateReader
# ---------------------------------------------------------------------------

class StateReader:
    """Domain-aware state file reader with vault policy enforcement.

    Usage
    -----
    reader = StateReader()

    # Read a named domain — enforces vault policy automatically:
    text = reader.read("finance")

    # Read a raw path — raises VaultPolicyViolation if vault-required:
    text = reader.read_raw("state/goals.md")
    """

    def read(self, domain_name: str) -> str:
        """Return the decrypted text content of *domain_name*'s state file.

        If the domain has requires_vault: true, this method attempts age-cli
        decryption via scripts/vault.py decrypt.  If vault tooling is
        unavailable, raises VaultPolicyViolation (do NOT fall back to plaintext).

        If the domain has requires_vault: false, reads plaintext directly.

        Raises
        ------
        KeyError
            Domain not found in domain_registry.yaml.
        VaultPolicyViolation
            Vault-required domain but age-cli / key unavailable.
        FileNotFoundError
            State file does not exist.
        """
        cfg = _domain_cfg(domain_name)
        state_file = cfg.get("state_file", "")
        if not state_file:
            raise FileNotFoundError(
                f"Domain '{domain_name}' has no state_file in domain_registry.yaml"
            )
        abs_path = (_ARTHA_DIR / state_file).resolve()

        if cfg.get("requires_vault"):
            return self._decrypt(domain_name, abs_path)
        # Non-vault path — plain read
        return self._read_plain(abs_path)

    def read_raw(self, path: str | Path) -> str:
        """Read a state file by explicit path, enforcing vault policy.

        Raises VaultPolicyViolation if *path* corresponds to a vault-required
        domain's state file and is NOT the encrypted (.md.age) variant.  This
        prevents accidental plaintext reads of sensitive domains.

        Parameters
        ----------
        path:
            Absolute or repository-relative path to the state file.
        """
        abs_path = (_ARTHA_DIR / path).resolve()
        vault_paths = _vault_required_paths()
        if str(abs_path) in vault_paths:
            raise VaultPolicyViolation(
                f"read_raw() refused — '{path}' is a vault-required state file. "
                f"Use StateReader().read('<domain_name>') to auto-decrypt."
            )
        return self._read_plain(abs_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_plain(abs_path: Path) -> str:
        if not abs_path.exists():
            raise FileNotFoundError(f"State file not found: {abs_path}")
        return abs_path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _decrypt(domain_name: str, abs_path: Path) -> str:
        """Decrypt an age-encrypted state file via scripts/vault.py.

        DEBT-ARCH-001 Phase 1: subprocess delegation to vault.py so that
        keyring / age integration stays in one place.  Phase 2 will expose
        a proper Python API.

        Raises VaultPolicyViolation if decryption fails (never falls back
        to plaintext).
        """
        if not abs_path.exists():
            raise FileNotFoundError(
                f"Encrypted state file for domain '{domain_name}' not found: {abs_path}"
            )
        vault_script = _ARTHA_DIR / "scripts" / "vault.py"
        if not vault_script.exists():
            raise VaultPolicyViolation(
                f"Domain '{domain_name}' requires vault but scripts/vault.py is missing. "
                f"Run: git status — the vault script may be untracked."
            )
        try:
            result = subprocess.run(  # noqa: S603
                [sys.executable, str(vault_script), "decrypt", str(abs_path)],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired as exc:
            raise VaultPolicyViolation(
                f"Vault decryption for '{domain_name}' timed out (15s). "
                f"Check age-cli and keyring availability."
            ) from exc
        except OSError as exc:
            raise VaultPolicyViolation(
                f"Vault decryption for '{domain_name}' failed: {exc}"
            ) from exc

        if result.returncode != 0:
            raise VaultPolicyViolation(
                f"Vault decryption for '{domain_name}' returned exit code {result.returncode}. "
                f"stderr: {result.stderr.strip()[:200]}"
            )
        return result.stdout
