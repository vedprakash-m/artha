#!/usr/bin/env python3
"""
vault_hook.py — Claude Code hook helper for vault operations
=============================================================
Always exits 0 so hooks never block tool execution.
Called by .claude/settings.json PreToolUse and PostToolUse hooks.

Usage:
  python scripts/vault_hook.py decrypt       — silent decrypt attempt
  python scripts/vault_hook.py stray-check   — warn about unprotected plaintext

Ref: TS §3.6.2
RD-02: Decrypt failures now write a sentinel file instead of being swallowed,
       so Artha.core.md can enter Read-Only Mode for vault-protected domains.
"""

import os
import sys
import subprocess

ARTHA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ARTHA_DIR, "state")
LOCK_FILE = os.path.join(ARTHA_DIR, ".artha-decrypted")
VAULT_PY  = os.path.join(ARTHA_DIR, "scripts", "vault.py")

# RD-02: Sentinel file path for vault decrypt failures.
# Written on any decrypt failure so Artha.core.md can detect and enter
# Read-Only Mode. Cleared on successful decrypt. Kept outside the
# OneDrive-synced state/ dir to avoid cloud leakage.
_LOCAL_DIR = os.path.expanduser(os.environ.get("ARTHA_LOCAL_DIR", "~/.artha-local"))
_DECRYPT_FAILED_SENTINEL = os.path.join(_LOCAL_DIR, ".artha-decrypt-failed")

# DEBT-002: Single source of truth for sensitive domains.
# Import from foundation.py (which now exports get_sensitive_domains()).
# Fallback: full 12-entry static literal used when venv is unavailable
# (bare Git hook context).
# AUTO-VALIDATED by tests/unit/test_vault.py::test_vault_hook_fallback_complete
try:
    import sys as _sys
    _scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if _scripts_dir not in _sys.path:
        _sys.path.insert(0, _scripts_dir)
    from foundation import get_sensitive_domains as _get_sensitive_domains
    SENSITIVE_DOMAINS = list(_get_sensitive_domains())
except Exception:  # noqa: BLE001
    # Static fallback — must enumerate ALL 12 domains explicitly.
    # This list MUST be kept in sync with foundation.py SENSITIVE_FILES.
    # DEBT-VAULT-001: Added 'employment' (salary, RSU, comp data) — was missing.
    SENSITIVE_DOMAINS = [
        "immigration", "finance", "insurance", "estate", "health",
        "audit", "vehicle", "contacts", "occasions", "transactions",
        "kids", "employment",  # DEBT-006 — salary, RSU, comp data
    ]


def hook_decrypt() -> None:
    """Attempt to decrypt vault; always succeed for hook safety.

    RD-02: On any failure, writes a sentinel file so Artha.core.md can
    detect vault failure and enter Read-Only Mode for protected domains.
    On success, clears the sentinel. Always exits 0 (hook contract).
    """
    # Ensure local dir exists (may not exist on first run)
    try:
        os.makedirs(_LOCAL_DIR, exist_ok=True)
    except OSError:
        pass

    # Clear any prior failure sentinel before attempting decrypt
    try:
        if os.path.exists(_DECRYPT_FAILED_SENTINEL):
            os.unlink(_DECRYPT_FAILED_SENTINEL)
    except OSError:
        pass

    try:
        result = subprocess.run(
            [sys.executable, VAULT_PY, "decrypt"],
            capture_output=True, timeout=90, cwd=ARTHA_DIR,
        )
        if result.returncode != 0:
            error_text = result.stderr.decode("utf-8", errors="replace")[:500]
            _write_sentinel(f"returncode={result.returncode}\n{error_text}")
            print(
                f"[VAULT] Decrypt FAILED (rc={result.returncode}). "
                f"Sentinel written to {_DECRYPT_FAILED_SENTINEL}. "
                "Session will use READ-ONLY mode for vault-protected domains.",
                file=sys.stderr,
            )
        else:
            print("[VAULT] Decrypt succeeded.", file=sys.stderr)
    except subprocess.TimeoutExpired:
        _write_sentinel("TIMEOUT: decrypt subprocess exceeded 90s")
        print("[VAULT] Decrypt TIMED OUT. Sentinel written.", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        _write_sentinel(f"EXCEPTION: {exc}")
        print(f"[VAULT] Decrypt ERROR: {exc}. Sentinel written.", file=sys.stderr)


def _write_sentinel(content: str) -> None:
    """Write the decrypt-failed sentinel file atomically."""
    import tempfile as _tmp
    try:
        fd, tmp_path = _tmp.mkstemp(dir=_LOCAL_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.replace(tmp_path, _DECRYPT_FAILED_SENTINEL)
    except OSError as exc:
        print(f"[VAULT-WARN] Could not write sentinel: {exc}", file=sys.stderr)


def hook_stray_check() -> None:
    """Warn if plaintext sensitive files exist without a lock file."""
    if os.path.exists(LOCK_FILE):
        return  # Active session — plaintext is expected
    for domain in SENSITIVE_DOMAINS:
        plain = os.path.join(STATE_DIR, f"{domain}.md")
        if os.path.exists(plain):
            print(f"[VAULT-WARN] Stray plaintext: {plain} (no lock file)")


def hook_contacts_integrity() -> None:
    """DEBT-TRUST-001: Check contacts.yaml for tampering via modification timestamp.

    Records the contacts.yaml mtime in state/.contacts_integrity_ts.json on
    first call.  On subsequent calls, warns if the mtime has changed since the
    last recorded value (indicating an out-of-vault edit).

    Uses mtime only — does not read contact data, so no PII is accessed here.
    """
    import json as _json_ci
    contacts_path = os.path.join(STATE_DIR, "contacts.yaml")
    sentinel_path = os.path.join(STATE_DIR, ".contacts_integrity_ts.json")

    if not os.path.exists(contacts_path):
        return  # no contacts.yaml — nothing to check

    try:
        current_mtime = os.path.getmtime(contacts_path)
    except OSError:
        return

    try:
        if os.path.exists(sentinel_path):
            sentinel = _json_ci.loads(open(sentinel_path).read())
            recorded_mtime = float(sentinel.get("mtime", 0))
            recorded_sha = sentinel.get("sha256", "")
            if recorded_mtime and abs(current_mtime - recorded_mtime) > 1.0:
                # mtime changed — compute sha256 to confirm actual content change
                import hashlib
                with open(contacts_path, "rb") as f:
                    current_sha = hashlib.sha256(f.read()).hexdigest()
                if current_sha != recorded_sha:
                    print(
                        f"[TRUST-WARN] contacts.yaml modified outside vault session "
                        f"(DEBT-TRUST-001). Last known: {recorded_mtime:.0f}, "
                        f"current: {current_mtime:.0f}. Verify no unauthorised edit."
                    )
                    # Update sentinel with new mtime+sha so we only warn once per change
                    _write_contacts_sentinel(sentinel_path, current_mtime, current_sha)
                    return
        # First call or hash matched — record current state
        import hashlib
        with open(contacts_path, "rb") as f:
            current_sha = hashlib.sha256(f.read()).hexdigest()
        _write_contacts_sentinel(sentinel_path, current_mtime, current_sha)
    except Exception:  # noqa: BLE001
        pass  # integrity checks are best-effort — never block hooks


def _write_contacts_sentinel(sentinel_path: str, mtime: float, sha256: str) -> None:
    """Write contacts integrity sentinel atomically."""
    import json as _json_ci
    import tempfile as _tmp_ci
    payload = {"mtime": mtime, "sha256": sha256}
    try:
        fd, tmp = _tmp_ci.mkstemp(dir=os.path.dirname(sentinel_path), suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            _json_ci.dump(payload, fh)
        os.replace(tmp, sentinel_path)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "decrypt":
        hook_decrypt()
    elif cmd == "stray-check":
        hook_stray_check()
    elif cmd == "contacts-integrity":
        hook_contacts_integrity()
    # Always exit 0 — hooks must not block tool execution
    sys.exit(0)


if __name__ == "__main__":
    main()
