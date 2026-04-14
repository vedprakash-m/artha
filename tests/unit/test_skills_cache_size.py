"""tests/unit/test_skills_cache_size.py — DEBT-028 skills cache size governance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make scripts/ importable without running the full bootstrap
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))

from skill_runner import _enforce_cache_size_cap, _CACHE_MAX_BYTES


def _make_fat_cache(n_skills: int, entry_kb: int = 5) -> dict:
    """Build a fake cache with *n_skills* entries each ~*entry_kb* KB."""
    payload = "x" * (entry_kb * 1024)
    cache = {}
    for i in range(n_skills):
        cache[f"skill_{i:04d}"] = {
            "last_run": f"2026-01-{(i % 28) + 1:02d}T00:00:00+00:00",
            "data": payload,
        }
    return cache


class TestCacheSizeCap:
    def test_small_cache_unchanged(self):
        """Cache under 1MB is returned as-is (no eviction)."""
        cache = {"skill_a": {"last_run": "2026-01-01T00:00:00+00:00", "data": "small"}}
        result = _enforce_cache_size_cap(cache)
        assert result == cache

    def test_oversized_cache_reduced(self):
        """Cache exceeding 1MB is reduced below the cap."""
        fat = _make_fat_cache(n_skills=300, entry_kb=5)
        assert len(json.dumps(fat, indent=2).encode()) > _CACHE_MAX_BYTES, "precondition"

        result = _enforce_cache_size_cap(fat)
        assert len(json.dumps(result, indent=2).encode()) <= _CACHE_MAX_BYTES

    def test_oldest_evicted_first(self):
        """Eviction follows FIFO (oldest last_run removed first)."""
        cache = {
            "old_skill": {"last_run": "2020-01-01T00:00:00+00:00", "data": "x" * 600_000},
            "new_skill": {"last_run": "2026-01-01T00:00:00+00:00", "data": "y" * 600_000},
        }
        # Both together exceed 1MB; only old_skill should be evicted
        assert len(json.dumps(cache, indent=2).encode()) > _CACHE_MAX_BYTES, "precondition"
        result = _enforce_cache_size_cap(cache)
        # old_skill (2020) should be evicted; new_skill (2026) should survive
        assert "new_skill" in result
        assert "old_skill" not in result

    def test_1000_writes_stays_bounded(self):
        """Simulate 1000 successive writes; result must always stay ≤ 1MB."""
        cache: dict = {}
        for i in range(1000):
            cache[f"skill_{i:04d}"] = {
                "last_run": f"2026-01-01T{i % 24:02d}:00:00+00:00",
                "data": "z" * 2048,
            }
            cache = _enforce_cache_size_cap(cache)
            size = len(json.dumps(cache, indent=2).encode())
            assert size <= _CACHE_MAX_BYTES, f"Exceeded 1MB at iteration {i}: {size} bytes"

    def test_original_dict_not_mutated(self):
        """_enforce_cache_size_cap must not mutate its input."""
        fat = _make_fat_cache(n_skills=300, entry_kb=5)
        original_keys = set(fat.keys())
        _enforce_cache_size_cap(fat)
        assert set(fat.keys()) == original_keys, "Input dict was mutated"
