"""
Unit tests for scripts/detect_environment.py

Coverage:
  - _classify_environment(): all platform + signal combos
  - _build_degradations(): all capability flag combinations
  - detect(): full integration with mocked probes
  - detect_json(): CLI output format validation
  - detect_cached(): cache read/write behavior
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import detect_environment as de


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_capable_signals(platform: str = "Darwin") -> dict:
    """Return a fully-capable signal dict for the given platform."""
    return {
        "cowork_marker":       False,
        "filesystem_writable": True,
        "age_installed":       True,
        "keyring_functional":  True,
        "network_google":      True,
        "network_microsoft":   True,
        "network_apple":       True,
        "platform":            platform,
    }


def _all_capable_caps() -> dict:
    """Return a fully-capable capabilities dict."""
    return {
        "filesystem_writable": True,
        "age_installed":       True,
        "keyring_functional":  True,
        "network_google":      True,
        "network_microsoft":   True,
        "network_apple":       True,
    }


# ---------------------------------------------------------------------------
# _classify_environment
# ---------------------------------------------------------------------------

class TestClassifyEnvironment:
    def test_mac(self):
        assert de._classify_environment(_all_capable_signals("Darwin")) == "local_mac"

    def test_windows(self):
        assert de._classify_environment(_all_capable_signals("Windows")) == "local_windows"

    def test_linux_all_capable(self):
        assert de._classify_environment(_all_capable_signals("Linux")) == "local_linux"

    def test_linux_readonly_fs_becomes_cowork_vm(self):
        s = {**_all_capable_signals("Linux"), "filesystem_writable": False}
        assert de._classify_environment(s) == "cowork_vm"

    def test_linux_no_age_no_keyring_becomes_unknown(self):
        s = {**_all_capable_signals("Linux"),
             "age_installed": False, "keyring_functional": False}
        assert de._classify_environment(s) == "unknown"

    def test_cowork_marker_overrides_mac(self):
        s = {**_all_capable_signals("Darwin"), "cowork_marker": True}
        assert de._classify_environment(s) == "cowork_vm"

    def test_cowork_marker_overrides_windows(self):
        s = {**_all_capable_signals("Windows"), "cowork_marker": True}
        assert de._classify_environment(s) == "cowork_vm"

    def test_cowork_marker_overrides_linux_capable(self):
        s = {**_all_capable_signals("Linux"), "cowork_marker": True}
        assert de._classify_environment(s) == "cowork_vm"

    def test_unknown_platform_defaults_unknown(self):
        s = {**_all_capable_signals("FreeBSD")}
        assert de._classify_environment(s) == "unknown"

    def test_empty_platform_defaults_unknown(self):
        s = {**_all_capable_signals("")}
        assert de._classify_environment(s) == "unknown"


# ---------------------------------------------------------------------------
# _build_degradations
# ---------------------------------------------------------------------------

class TestBuildDegradations:
    def test_all_capable_yields_no_degradations(self):
        assert de._build_degradations(_all_capable_caps()) == []

    def test_readonly_fs_sets_multiple_degradations(self):
        caps = {**_all_capable_caps(), "filesystem_writable": False}
        d = de._build_degradations(caps)
        assert "vault_decrypt_unavailable" in d
        assert "state_writes_disabled" in d
        assert "audit_log_disabled" in d

    def test_no_age_sets_encrypted_unavailable(self):
        caps = {**_all_capable_caps(), "age_installed": False}
        assert "encrypted_state_inaccessible" in de._build_degradations(caps)

    def test_no_keyring_sets_credential_store_unavailable(self):
        caps = {**_all_capable_caps(), "keyring_functional": False}
        assert "credential_store_unavailable" in de._build_degradations(caps)

    def test_no_network_microsoft(self):
        caps = {**_all_capable_caps(), "network_microsoft": False}
        d = de._build_degradations(caps)
        assert "outlook_mail_unavailable" in d
        assert "ms_todo_sync_unavailable" in d

    def test_no_network_apple(self):
        caps = {**_all_capable_caps(), "network_apple": False}
        d = de._build_degradations(caps)
        assert "icloud_mail_unavailable" in d
        assert "icloud_calendar_unavailable" in d

    def test_cowork_vm_all_degraded(self):
        caps = {
            "filesystem_writable": False,
            "age_installed":       False,
            "keyring_functional":  False,
            "network_google":      True,   # Google works in Cowork VM
            "network_microsoft":   False,
            "network_apple":       False,
        }
        d = de._build_degradations(caps)
        assert len(d) >= 6  # at least 6 degradations


# ---------------------------------------------------------------------------
# detect() — integration with mocked probes
# ---------------------------------------------------------------------------

class TestDetect:
    """Mock all I/O probes so tests run without filesystem or network access."""

    def _patch_probes(
        self,
        cowork=(False, "absent"),
        fs=(True, "writable"),
        age=(True, "/usr/bin/age"),
        keyring=(True, "functional"),
        network=(True, "reachable"),
    ):
        return (
            patch("detect_environment._probe_cowork_marker",       return_value=cowork),
            patch("detect_environment._probe_filesystem_writable", return_value=fs),
            patch("detect_environment._probe_age_installed",       return_value=age),
            patch("detect_environment._probe_keyring_functional",  return_value=keyring),
            patch("detect_environment._probe_network",             return_value=network),
        )

    def test_returns_manifest_object(self):
        with (
            patch("detect_environment._probe_cowork_marker",       return_value=(False, "absent")),
            patch("detect_environment._probe_filesystem_writable", return_value=(True,  "writable")),
            patch("detect_environment._probe_age_installed",       return_value=(True,  "/usr/bin/age")),
            patch("detect_environment._probe_keyring_functional",  return_value=(True,  "functional")),
        ):
            manifest = de.detect(skip_network=True)

        assert isinstance(manifest, de.EnvironmentManifest)
        assert manifest.environment in ("local_mac", "local_windows", "local_linux",
                                        "cowork_vm", "unknown")
        assert isinstance(manifest.capabilities, dict)
        assert isinstance(manifest.degradations, list)
        assert manifest.probed_at  # non-empty timestamp

    def test_skip_network_does_not_call_probe_network(self):
        with (
            patch("detect_environment._probe_cowork_marker",       return_value=(False, "absent")),
            patch("detect_environment._probe_filesystem_writable", return_value=(True, "writable")),
            patch("detect_environment._probe_age_installed",       return_value=(True, "/usr/bin/age")),
            patch("detect_environment._probe_keyring_functional",  return_value=(True, "functional")),
            patch("detect_environment._probe_network") as mock_net,
        ):
            manifest = de.detect(skip_network=True)

        mock_net.assert_not_called()
        # Skipped network caps are optimistic (True)
        assert manifest.capabilities["network_google"] is True
        assert manifest.capabilities["network_microsoft"] is True

    def test_cowork_vm_full_degradation(self):
        with (
            patch("detect_environment._probe_cowork_marker",
                  return_value=(True, "env:COWORK_SESSION_ID=abc123...")),
            patch("detect_environment._probe_filesystem_writable",
                  return_value=(False, "read_only:PermissionError")),
            patch("detect_environment._probe_age_installed",
                  return_value=(False, "not_found")),
            patch("detect_environment._probe_keyring_functional",
                  return_value=(False, "keyring_not_installed")),
            patch("detect_environment._probe_network",
                  return_value=(False, "blocked:ConnectionRefusedError")),
        ):
            manifest = de.detect(skip_network=False)

        assert manifest.environment == "cowork_vm"
        assert manifest.capabilities["filesystem_writable"] is False
        assert "vault_decrypt_unavailable" in manifest.degradations
        assert "encrypted_state_inaccessible" in manifest.degradations
        assert "outlook_mail_unavailable" in manifest.degradations

    def test_to_dict_excludes_signals_by_default(self):
        with (
            patch("detect_environment._probe_cowork_marker",       return_value=(False, "absent")),
            patch("detect_environment._probe_filesystem_writable", return_value=(True, "writable")),
            patch("detect_environment._probe_age_installed",       return_value=(True, "/usr/bin/age")),
            patch("detect_environment._probe_keyring_functional",  return_value=(True, "functional")),
        ):
            manifest = de.detect(skip_network=True)

        d = manifest.to_dict(include_signals=False)
        assert "detection_signals" not in d
        assert "environment"  in d
        assert "capabilities" in d
        assert "degradations" in d
        assert "probed_at"    in d

    def test_to_dict_includes_signals_when_requested(self):
        with (
            patch("detect_environment._probe_cowork_marker",       return_value=(False, "absent")),
            patch("detect_environment._probe_filesystem_writable", return_value=(True, "writable")),
            patch("detect_environment._probe_age_installed",       return_value=(True, "/usr/bin/age")),
            patch("detect_environment._probe_keyring_functional",  return_value=(True, "functional")),
        ):
            manifest = de.detect(skip_network=True)

        d = manifest.to_dict(include_signals=True)
        assert "detection_signals" in d
        assert "platform_raw" in d["detection_signals"]

    def test_mac_has_no_degradations(self):
        with (
            patch("detect_environment._probe_cowork_marker",       return_value=(False, "absent")),
            patch("detect_environment._probe_filesystem_writable", return_value=(True, "writable")),
            patch("detect_environment._probe_age_installed",       return_value=(True, "/usr/bin/age")),
            patch("detect_environment._probe_keyring_functional",  return_value=(True, "functional")),
        ):
            manifest = de.detect(skip_network=True)

        # On any fully-capable local environment, degradations should be empty
        assert manifest.degradations == []


# ---------------------------------------------------------------------------
# detect_json() — validates CLI output format
# ---------------------------------------------------------------------------

class TestDetectJson:
    def test_returns_valid_json(self):
        with (
            patch("detect_environment._probe_cowork_marker",       return_value=(False, "absent")),
            patch("detect_environment._probe_filesystem_writable", return_value=(True, "writable")),
            patch("detect_environment._probe_age_installed",       return_value=(True, "/usr/bin/age")),
            patch("detect_environment._probe_keyring_functional",  return_value=(True, "functional")),
        ):
            result = de.detect_json(skip_network=True)

        parsed = json.loads(result)
        assert "environment"  in parsed
        assert "capabilities" in parsed
        assert "degradations" in parsed

    def test_debug_mode_includes_signals(self):
        with (
            patch("detect_environment._probe_cowork_marker",       return_value=(False, "absent")),
            patch("detect_environment._probe_filesystem_writable", return_value=(True, "writable")),
            patch("detect_environment._probe_age_installed",       return_value=(True, "/usr/bin/age")),
            patch("detect_environment._probe_keyring_functional",  return_value=(True, "functional")),
        ):
            result = de.detect_json(debug=True, skip_network=True)

        parsed = json.loads(result)
        assert "detection_signals" in parsed

    def test_non_debug_mode_excludes_signals(self):
        with (
            patch("detect_environment._probe_cowork_marker",       return_value=(False, "absent")),
            patch("detect_environment._probe_filesystem_writable", return_value=(True, "writable")),
            patch("detect_environment._probe_age_installed",       return_value=(True, "/usr/bin/age")),
            patch("detect_environment._probe_keyring_functional",  return_value=(True, "functional")),
        ):
            result = de.detect_json(debug=False, skip_network=True)

        parsed = json.loads(result)
        assert "detection_signals" not in parsed


# ---------------------------------------------------------------------------
# detect_cached() — cache behavior
# ---------------------------------------------------------------------------

class TestDetectCached:
    def test_fresh_probe_writes_cache(self, tmp_path, monkeypatch):
        cache_file = tmp_path / ".env_manifest.json"
        monkeypatch.setattr(de, "_TMP_DIR",    tmp_path)
        monkeypatch.setattr(de, "_CACHE_FILE", cache_file)

        with (
            patch("detect_environment._probe_cowork_marker",       return_value=(False, "absent")),
            patch("detect_environment._probe_filesystem_writable", return_value=(True, "writable")),
            patch("detect_environment._probe_age_installed",       return_value=(True, "/usr/bin/age")),
            patch("detect_environment._probe_keyring_functional",  return_value=(True, "functional")),
        ):
            de.detect_cached(force_refresh=True, skip_network=True)

        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert "environment" in data

    def test_fresh_cache_is_reused(self, tmp_path, monkeypatch):
        cache_file = tmp_path / ".env_manifest.json"
        monkeypatch.setattr(de, "_TMP_DIR",    tmp_path)
        monkeypatch.setattr(de, "_CACHE_FILE", cache_file)

        cached_data = {
            "environment":       "local_mac",
            "capabilities":      _all_capable_caps(),
            "degradations":      [],
            "detection_signals": {},
            "probed_at":         "2026-03-15T00:00:00+00:00",
        }
        cache_file.write_text(json.dumps(cached_data))

        with patch("detect_environment.detect") as mock_detect:
            de.detect_cached(force_refresh=False, skip_network=True)

        mock_detect.assert_not_called()

    def test_force_refresh_bypasses_cache(self, tmp_path, monkeypatch):
        cache_file = tmp_path / ".env_manifest.json"
        monkeypatch.setattr(de, "_TMP_DIR",    tmp_path)
        monkeypatch.setattr(de, "_CACHE_FILE", cache_file)

        stale_data = {
            "environment":       "cowork_vm",    # stale value
            "capabilities":      _all_capable_caps(),
            "degradations":      [],
            "detection_signals": {},
            "probed_at":         "2026-01-01T00:00:00+00:00",
        }
        cache_file.write_text(json.dumps(stale_data))

        with (
            patch("detect_environment._probe_cowork_marker",       return_value=(False, "absent")),
            patch("detect_environment._probe_filesystem_writable", return_value=(True, "writable")),
            patch("detect_environment._probe_age_installed",       return_value=(True, "/usr/bin/age")),
            patch("detect_environment._probe_keyring_functional",  return_value=(True, "functional")),
        ):
            result = de.detect_cached(force_refresh=True, skip_network=True)

        # Should be fresh, not "cowork_vm" from cache
        assert result.environment != "cowork_vm"
