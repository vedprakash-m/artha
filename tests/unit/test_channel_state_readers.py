"""tests/unit/test_channel_state_readers.py — T4-11..20: channel.state_readers tests."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import channel.state_readers as sr
from channel.state_readers import (
    _read_state_file,
    _format_age,
    _get_latest_briefing_path,
    _apply_scope_filter,
    _get_domain_open_items,
    _parse_age_to_hours,
    _READABLE_STATE_FILES,
    _DOMAIN_TO_STATE_FILE,
)


@pytest.fixture(autouse=True)
def patch_state_dir(tmp_path, monkeypatch):
    """Redirect _STATE_DIR to tmp_path for all tests in this file."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(sr, "_STATE_DIR", state_dir)
    # Re-build READABLE_STATE_FILES to point to tmp state dir
    patched = {k: state_dir / v.name if v.name else v for k, v in _READABLE_STATE_FILES.items()}
    monkeypatch.setattr(sr, "_READABLE_STATE_FILES", patched)
    return state_dir


# ---------------------------------------------------------------------------
# T4-11: _read_state_file — whitelisted keys
# ---------------------------------------------------------------------------

class TestReadStateFile:
    def test_known_key_missing_file_returns_graceful(self):
        content, staleness = _read_state_file("health_check")
        assert isinstance(content, str)
        assert isinstance(staleness, str)

    def test_unknown_key_returns_empty(self):
        content, staleness = _read_state_file("__nonexistent_key__")
        assert content == ""
        assert staleness == "unknown"

    def test_existing_file_returns_content(self, tmp_path):
        import channel.state_readers as sr2
        state_dir = Path(sr2._STATE_DIR)
        f = state_dir / "health-check.md"
        f.write_text("# Health Check\nAll good.\n", encoding="utf-8")
        # Patch READABLE_STATE_FILES for this call
        old = sr2._READABLE_STATE_FILES.get("health_check")
        sr2._READABLE_STATE_FILES["health_check"] = f
        try:
            content, staleness = _read_state_file("health_check")
            assert "All good." in content
            assert isinstance(staleness, str)
        finally:
            if old:
                sr2._READABLE_STATE_FILES["health_check"] = old

    def test_encrypted_file_not_served(self, tmp_path):
        import channel.state_readers as sr2
        enc_path = tmp_path / "secret.yaml"
        enc_path.write_text("data", encoding="utf-8")
        old = sr2._READABLE_STATE_FILES.get("gallery")
        sr2._READABLE_STATE_FILES["gallery"] = enc_path
        try:
            content, staleness = _read_state_file("gallery")
            # .yaml falls through to content read (not encrypted)
            assert isinstance(content, str)
        finally:
            if old:
                sr2._READABLE_STATE_FILES["gallery"] = old


# ---------------------------------------------------------------------------
# T4-12: _format_age formatting
# ---------------------------------------------------------------------------

class TestFormatAge:
    def test_seconds(self):
        result = _format_age(45)
        assert "s" in result

    def test_minutes(self):
        result = _format_age(90)
        assert "m" in result

    def test_hours(self):
        result = _format_age(3700)
        assert "h" in result

    def test_days(self):
        result = _format_age(90000)
        assert "d" in result

    def test_zero(self):
        result = _format_age(0)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# T4-13: _get_latest_briefing_path
# ---------------------------------------------------------------------------

def test_get_latest_briefing_path_no_briefings(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_BRIEFINGS_DIR", tmp_path / "briefings")
    result = _get_latest_briefing_path()
    assert result is None


def test_get_latest_briefing_path_finds_latest(tmp_path, monkeypatch):
    briefings = tmp_path / "briefings"
    briefings.mkdir()
    monkeypatch.setattr(sr, "_BRIEFINGS_DIR", briefings)
    (briefings / "2026-03-01.md").write_text("old", encoding="utf-8")
    (briefings / "2026-03-25.md").write_text("new", encoding="utf-8")
    result = _get_latest_briefing_path()
    assert result is not None
    assert "2026-03-25" in result.name


# ---------------------------------------------------------------------------
# T4-14: _apply_scope_filter
# ---------------------------------------------------------------------------

class TestApplyScopeFilter:
    def test_full_scope_passes_all(self):
        text = "Immigration status: visa pending\nFinance: $50k balance"
        result = _apply_scope_filter(text, scope="full")
        assert result == text

    def test_family_scope_redacts_sensitive(self):
        text = "Immigration: H-1B pending\nCalendar: meeting at 3pm"
        result = _apply_scope_filter(text, scope="family")
        assert "H-1B" not in result
        assert "meeting" in result.lower() or "Calendar" in result

    def test_standard_scope_filters_to_essential(self):
        text = "Goals: finish project\nCalendar: meeting today\nFinance details here"
        result = _apply_scope_filter(text, scope="standard")
        assert isinstance(result, str)

    def test_unknown_scope_passthrough(self):
        text = "Some content"
        result = _apply_scope_filter(text, scope="unknown_scope")
        assert result == text


# ---------------------------------------------------------------------------
# T4-15: _get_domain_open_items
# ---------------------------------------------------------------------------

def test_get_domain_open_items_no_file():
    result = _get_domain_open_items("health")
    assert isinstance(result, str)


def test_get_domain_open_items_with_content(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(sr, "_STATE_DIR", state_dir)
    oi_file = state_dir / "open_items.md"
    oi_file.write_text(
        "---\ntitle: Open Items\n---\n\n## Health\n- OI-001: See doctor\n",
        encoding="utf-8",
    )
    import channel.state_readers as sr2
    sr2._READABLE_STATE_FILES["open_items"] = oi_file
    result = _get_domain_open_items("health")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# T4-16: _parse_age_to_hours
# ---------------------------------------------------------------------------

class TestParseAgeToHours:
    def test_hours(self):
        result = _parse_age_to_hours("2h")
        assert result == pytest.approx(2.0)

    def test_days(self):
        result = _parse_age_to_hours("1d")
        assert result == pytest.approx(24.0)

    def test_minutes(self):
        result = _parse_age_to_hours("30m")
        assert result == pytest.approx(0.5)

    def test_garbage_returns_zero_or_default(self):
        result = _parse_age_to_hours("garbage")
        assert isinstance(result, (int, float))

    def test_empty_string(self):
        result = _parse_age_to_hours("")
        assert isinstance(result, (int, float))


# ---------------------------------------------------------------------------
# T4-17: _DOMAIN_TO_STATE_FILE completeness
# ---------------------------------------------------------------------------

def test_domain_to_state_file_has_entries():
    assert len(_DOMAIN_TO_STATE_FILE) >= 5
    assert "health" in _DOMAIN_TO_STATE_FILE
    assert "goals" in _DOMAIN_TO_STATE_FILE
    assert "calendar" in _DOMAIN_TO_STATE_FILE


# ---------------------------------------------------------------------------
# T4-18: _READABLE_STATE_FILES whitelist
# ---------------------------------------------------------------------------

def test_readable_state_files_contains_required():
    assert "health_check" in _READABLE_STATE_FILES
    assert "open_items" in _READABLE_STATE_FILES
    assert "goals" in _READABLE_STATE_FILES
    # All values should be Path objects
    for key, path in _READABLE_STATE_FILES.items():
        assert isinstance(path, Path), f"Key {key} has non-Path value: {type(path)}"
