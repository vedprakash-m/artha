#!/usr/bin/env python3
"""
scripts/setup_ha_token.py — Interactive Home Assistant integration setup wizard.

Prompts for HA URL and long-lived access token, validates connectivity,
stores the token in the system keyring, updates connectors.yaml, and
enables the homeassistant connector.

Usage:
    python scripts/setup_ha_token.py

Steps:
    1. Prompt for HA URL        (default: http://192.168.1.123:8123)
    2. Prompt for long-lived token (masked input via getpass)
    3. Validate: GET /api/ with token → must return {"message": "API running."}
    4. Store token in system keyring as service="artha-ha-token", account="artha"
    5. Update config/connectors.yaml → homeassistant.fetch.ha_url
    6. Set homeassistant.enabled = true
    7. Create tmp/.nosync (prevent OneDrive sync of ephemeral temp files)
    8. Print success + recommended next steps

Rollback:
    On any failure before step 4 (keyring write), no persistent state is
    changed.  After step 4, the token can be removed with:
        python -c "import keyring; keyring.delete_password('artha-ha-token', 'artha')"
    And connectors.yaml can be reset by setting enabled: false, ha_url: "".

Ref: specs/iot.md §3.4
"""
from __future__ import annotations

import getpass
import json
import re
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Bootstrap path
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass

import requests  # type: ignore[import]
import yaml      # type: ignore[import]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CONNECTORS_YAML = _REPO_ROOT / "config" / "connectors.yaml"
_TMP_DIR = _REPO_ROOT / "tmp"
_NOSYNC_FILE = _TMP_DIR / ".nosync"
_DEFAULT_HA_URL = "http://192.168.1.123:8123"
_KEYRING_SERVICE = "artha-ha-token"
_KEYRING_ACCOUNT = "artha"
_REQUEST_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_url(url: str) -> str:
    """Normalise and validate the HA URL. Returns cleaned URL or raises ValueError."""
    url = url.strip().rstrip("/")
    if not url:
        raise ValueError("URL cannot be empty.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL must start with http:// or https://  Got: {url!r}")
    if not parsed.hostname:
        raise ValueError(f"URL has no hostname: {url!r}")
    return url


def _test_ha_connection(ha_url: str, token: str, timeout: int = _REQUEST_TIMEOUT) -> dict:
    """Call GET /api/ and return the JSON body.

    Raises RuntimeError with a user-friendly message on any failure.
    """
    url = f"{ha_url}/api/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to {ha_url}. "
            "Make sure you are on the home network and HA is running."
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"Connection to {ha_url} timed out ({timeout}s). "
            "Check that HA is running and the URL port is correct."
        )

    if resp.status_code == 401:
        raise RuntimeError(
            "Authentication failed (HTTP 401). "
            "The token is invalid or expired. Create a new long-lived token in HA:\n"
            "  HA UI → Profile → Security → Long-Lived Access Tokens → Create Token"
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HA returned HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"HA returned non-JSON response: {exc}") from exc

    if data.get("message") != "API running.":
        raise RuntimeError(
            f"Unexpected HA API response: {data}. "
            "Expected {{\"message\": \"API running.\"}}"
        )
    return data


def _store_token(token: str) -> None:
    """Store the token in the system keyring."""
    try:
        import keyring  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            f"keyring not installed: {exc}. Run: pip install keyring"
        ) from exc
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, token)


def _update_connectors_yaml(ha_url: str) -> None:
    """Set ha_url and enabled=true in config/connectors.yaml atomically.

    Reads the YAML, modifies the homeassistant block in-place, and writes
    back using a temp-then-rename pattern to avoid partial writes.
    """
    if not _CONNECTORS_YAML.exists():
        raise RuntimeError(f"connectors.yaml not found at {_CONNECTORS_YAML}")

    with open(_CONNECTORS_YAML, encoding="utf-8") as fh:
        content = fh.read()
        cfg = yaml.safe_load(content)

    connectors = cfg.get("connectors", {})
    ha_block = connectors.get("homeassistant")
    if ha_block is None:
        raise RuntimeError(
            "homeassistant block not found in connectors.yaml. "
            "The connector may not be registered yet."
        )

    # Mutate in-place
    ha_block["enabled"] = True
    fetch_block = ha_block.setdefault("fetch", {})
    fetch_block["ha_url"] = ha_url

    # Write back atomically using ruamel.yaml to preserve comments, or fall
    # back to line-by-line string substitution if ruamel not available.
    _write_yaml_atomic(cfg)


def _write_yaml_atomic(cfg: dict) -> None:
    """Write cfg to connectors.yaml atomically (temp + rename)."""
    import tempfile

    # Try ruamel for comment-preserving round-trip
    try:
        from ruamel.yaml import YAML  # type: ignore[import]  # optional dependency
        ryaml = YAML()
        ryaml.preserve_quotes = True
        with open(_CONNECTORS_YAML, encoding="utf-8") as fh:
            original = ryaml.load(fh)
        # Update only the fields we want (nested path)
        original["connectors"]["homeassistant"]["enabled"] = True
        original["connectors"]["homeassistant"]["fetch"]["ha_url"] = \
            cfg["connectors"]["homeassistant"]["fetch"]["ha_url"]

        fd, tmp_path = tempfile.mkstemp(
            dir=_CONNECTORS_YAML.parent, suffix=".tmp", prefix=".connectors_"
        )
        import os
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                ryaml.dump(original, fh)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        os.replace(tmp_path, _CONNECTORS_YAML)
        return
    except ImportError:
        pass  # ruamel not available — fall back to PyYAML

    # PyYAML fallback — does not preserve comments, but is always available
    import os, tempfile as _tempfile
    fd, tmp_path = _tempfile.mkstemp(
        dir=_CONNECTORS_YAML.parent, suffix=".tmp", prefix=".connectors_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.dump(cfg, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    os.replace(tmp_path, _CONNECTORS_YAML)


def _create_nosync() -> None:
    """Create tmp/.nosync to prevent OneDrive from syncing ephemeral temp files."""
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    if not _NOSYNC_FILE.exists():
        _NOSYNC_FILE.touch()
        print(f"  ✓ Created {_NOSYNC_FILE.relative_to(_REPO_ROOT)} (blocks OneDrive sync of tmp/)")


# ---------------------------------------------------------------------------
# Main interactive flow
# ---------------------------------------------------------------------------

def main() -> int:
    print("\n═══════════════════════════════════════════════════════")
    print("   Artha — Home Assistant Integration Setup Wizard")
    print("═══════════════════════════════════════════════════════\n")
    print("This wizard will:")
    print("  1. Validate your HA URL and long-lived access token")
    print("  2. Store the token securely in your system keyring")
    print("  3. Enable the HA connector in connectors.yaml\n")
    print("Prerequisites:")
    print("  • You must be on the home network (LAN)")
    print("  • Create a long-lived token in HA:")
    print("    HA UI → Profile → Security → Long-Lived Access Tokens\n")

    # ── Step 1: HA URL ────────────────────────────────────────────────────
    print(f"Step 1/7: Home Assistant URL")
    print(f"  Default: {_DEFAULT_HA_URL}")
    raw_url = input("  Enter URL (press Enter to use default): ").strip()
    ha_url = raw_url if raw_url else _DEFAULT_HA_URL
    try:
        ha_url = _validate_url(ha_url)
    except ValueError as exc:
        print(f"\n  ✗ Invalid URL: {exc}")
        return 1
    print(f"  Using: {ha_url}\n")

    # ── Step 2: Long-lived token ──────────────────────────────────────────
    print("Step 2/7: Long-Lived Access Token")
    print("  (Input is hidden — paste your token and press Enter)")
    try:
        token = getpass.getpass("  Token: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n  Aborted.")
        return 1

    if not token:
        print("  ✗ Token cannot be empty.")
        return 1
    if len(token) < 20:
        print("  ✗ Token looks too short. HA tokens are typically 300+ characters.")
        return 1
    print(f"  ✓ Token received ({len(token)} chars)\n")

    # ── Step 3: Validate connectivity ────────────────────────────────────
    print(f"Step 3/7: Validating connectivity to {ha_url} …")
    try:
        data = _test_ha_connection(ha_url, token)
        ha_version = data.get("version", "unknown")
        print(f"  ✓ Connected! HA version: {ha_version}\n")
    except RuntimeError as exc:
        print(f"\n  ✗ Validation failed:\n    {exc}")
        print("\n  No changes were made. Fix the issue above and re-run.")
        return 1

    # ── Step 4: Store token in keyring ────────────────────────────────────
    print("Step 4/7: Storing token in system keyring …")
    try:
        _store_token(token)
        print(f"  ✓ Token stored (service: {_KEYRING_SERVICE!r}, account: {_KEYRING_ACCOUNT!r})\n")
    except RuntimeError as exc:
        print(f"\n  ✗ Keyring storage failed: {exc}")
        return 1

    # ── Step 5 + 6: Update connectors.yaml ───────────────────────────────
    print("Step 5/7: Updating config/connectors.yaml …")
    try:
        _update_connectors_yaml(ha_url)
        print("  ✓ ha_url set and connector enabled in connectors.yaml\n")
    except Exception as exc:
        print(f"\n  ✗ Failed to update connectors.yaml: {exc}")
        print(
            "\n  The token is stored in keyring but connectors.yaml was NOT updated.\n"
            "  Manual fix: set homeassistant.fetch.ha_url and homeassistant.enabled=true"
        )
        return 1

    # ── Step 7: Create tmp/.nosync ────────────────────────────────────────
    print("Step 6/7: Ensuring tmp/ is protected from OneDrive sync …")
    _create_nosync()
    print()

    # ── Step 8: Success summary ───────────────────────────────────────────
    print("Step 7/7: Done!\n")
    print("═══════════════════════════════════════════════════════")
    print("   ✓ Home Assistant integration CONFIGURED")
    print("═══════════════════════════════════════════════════════\n")
    print("Next steps:")
    print("  1. Test the connector:")
    print("     python scripts/pipeline.py --health --source homeassistant")
    print()
    print("  2. Run a full fetch (stay on home network):")
    print("     python scripts/pipeline.py --source homeassistant | head -20")
    print()
    print("  3. Enable the skill (after connector test passes):")
    print("     Edit config/skills.yaml → home_device_monitor → enabled: true")
    print()
    print("  4. Revoke this token later:")
    print("     HA UI → Profile → Security → Long-Lived Access Tokens → Revoke")
    print()
    print("  To remove integration:")
    print(f"    python -c \"import keyring; keyring.delete_password('{_KEYRING_SERVICE}', '{_KEYRING_ACCOUNT}')\"")
    print("    Set connectors.yaml → homeassistant → enabled: false")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
