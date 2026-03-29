"""tests/work/test_reflection_key.py — Tests for scripts/work/reflection_key.py

Sprint 0 acceptance criteria §3.1.5:
- All horizons have correct string values
- ReflectionKey properties: as_string, artifact_filename, history_entry_id
- already_exists() correctly checks filesystem (using tmp_path)
- history_entry_id is stable (same input → same output)
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work.reflection_key import Horizon, ReflectionKey


# ---------------------------------------------------------------------------
# Horizon enum
# ---------------------------------------------------------------------------

class TestHorizon:
    def test_all_expected_values_present(self):
        assert {h.value for h in Horizon} == {
            "daily", "weekly", "monthly", "quarterly", "semi_annual", "yearly"
        }

    def test_string_mixin(self):
        """Horizon is a str enum — horizon.value should equal the string."""
        assert Horizon.DAILY.value == "daily"
        assert Horizon.WEEKLY.value == "weekly"
        assert Horizon.QUARTERLY.value == "quarterly"

    def test_horizon_usable_as_dict_key(self):
        d = {Horizon.DAILY: 1, Horizon.WEEKLY: 2}
        assert d[Horizon.DAILY] == 1


# ---------------------------------------------------------------------------
# ReflectionKey basic properties
# ---------------------------------------------------------------------------

class TestReflectionKeyProperties:
    def test_as_string_format(self):
        key = ReflectionKey(Horizon.WEEKLY, "2026-W14")
        assert key.as_string == "weekly/2026-W14"

    def test_as_string_monthly(self):
        key = ReflectionKey(Horizon.MONTHLY, "2026-04")
        assert key.as_string == "monthly/2026-04"

    def test_as_string_quarterly(self):
        key = ReflectionKey(Horizon.QUARTERLY, "2026-Q2")
        assert key.as_string == "quarterly/2026-Q2"

    def test_as_string_daily(self):
        key = ReflectionKey(Horizon.DAILY, "2026-04-01")
        assert key.as_string == "daily/2026-04-01"

    def test_artifact_filename_weekly(self):
        key = ReflectionKey(Horizon.WEEKLY, "2026-W14")
        assert key.artifact_filename == "state/work/reflections/weekly-2026-W14.md"

    def test_artifact_filename_monthly(self):
        key = ReflectionKey(Horizon.MONTHLY, "2026-04")
        assert key.artifact_filename == "state/work/reflections/monthly-2026-04.md"

    def test_artifact_filename_quarterly(self):
        key = ReflectionKey(Horizon.QUARTERLY, "2026-Q2")
        assert key.artifact_filename == "state/work/reflections/quarterly-2026-Q2.md"

    def test_artifact_filename_daily(self):
        key = ReflectionKey(Horizon.DAILY, "2026-04-01")
        assert key.artifact_filename == "state/work/reflections/daily-2026-04-01.md"

    def test_str_representation(self):
        key = ReflectionKey(Horizon.WEEKLY, "2026-W14")
        assert str(key) == "weekly/2026-W14"

    def test_frozen_cannot_mutate(self):
        key = ReflectionKey(Horizon.WEEKLY, "2026-W14")
        with pytest.raises((AttributeError, TypeError)):
            key.period = "2026-W15"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# history_entry_id stability
# ---------------------------------------------------------------------------

class TestHistoryEntryId:
    def test_stable_within_session(self):
        key = ReflectionKey(Horizon.WEEKLY, "2026-W14")
        id1 = key.history_entry_id()
        id2 = key.history_entry_id()
        assert id1 == id2

    def test_length_is_12(self):
        key = ReflectionKey(Horizon.MONTHLY, "2026-04")
        assert len(key.history_entry_id()) == 12

    def test_content_matches_sha1(self):
        key = ReflectionKey(Horizon.WEEKLY, "2026-W14")
        expected = hashlib.sha1("weekly/2026-W14".encode()).hexdigest()[:12]
        assert key.history_entry_id() == expected

    def test_different_periods_different_ids(self):
        k1 = ReflectionKey(Horizon.WEEKLY, "2026-W14")
        k2 = ReflectionKey(Horizon.WEEKLY, "2026-W15")
        assert k1.history_entry_id() != k2.history_entry_id()

    def test_different_horizons_different_ids(self):
        k1 = ReflectionKey(Horizon.WEEKLY, "2026-W14")
        k2 = ReflectionKey(Horizon.MONTHLY, "2026-W14")  # Same period, different horizon
        assert k1.history_entry_id() != k2.history_entry_id()


# ---------------------------------------------------------------------------
# already_exists() filesystem check
# ---------------------------------------------------------------------------

class TestAlreadyExists:
    def test_returns_false_when_file_absent(self, tmp_path, monkeypatch):
        """already_exists() should return False when artifact file is absent."""
        key = ReflectionKey(Horizon.WEEKLY, "2026-W99")
        # Patch Path so artifact_filename resolves to tmp_path
        import work.reflection_key as rk_mod
        original_path = rk_mod.Path

        class PatchedPath:
            def __init__(self, *args):
                # Build tmp-rooted path for our specific artifact
                joined = "/".join(str(a) for a in args)
                self._p = tmp_path / joined.replace("state/work/reflections/", "")
                # Fall-through for non-artifact paths
                if "reflections" not in joined:
                    self._p = original_path(*args)

            def exists(self):
                return self._p.exists()

        # Direct test: we know artifact_filename returns relative path
        # Verify the file doesn't exist at that relative path from cwd
        artifact = Path(key.artifact_filename)
        if artifact.exists():
            pytest.skip("Artifact file exists in real workspace — skipping isolation test")
        assert not key.already_exists()

    def test_returns_true_when_file_present(self, tmp_path, monkeypatch, chdir_tmp):
        """already_exists() returns True when the artifact file is present."""
        key = ReflectionKey(Horizon.WEEKLY, "2026-W99")
        artifact = Path(key.artifact_filename)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("# test", encoding="utf-8")
        assert key.already_exists()


@pytest.fixture()
def chdir_tmp(tmp_path, monkeypatch):
    """Change cwd to tmp_path for tests that need relative-path filesystem checks."""
    monkeypatch.chdir(tmp_path)
