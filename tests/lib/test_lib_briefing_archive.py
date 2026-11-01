"""tests/lib/test_briefing_archive.py — Unit tests for briefing_archive.py.

Covers:
  - save() with runtime= stores runtime: in frontmatter (spec §3.1a)
  - _run_injection_check() raises RuntimeError on import failure (spec §3.1b)

Ref: specs/rebrief.md §3.1, §3.6
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import date
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure scripts/ and scripts/lib/ are importable
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_injection_detector(monkeypatch, *, detected: bool = False) -> None:
    """Install a fake InjectionDetector that returns a fixed detection result."""

    class _Result:
        injection_detected = detected

    class _FakeDetector:
        def scan(self, _text: str) -> _Result:
            return _Result()

    fake_mod = types.ModuleType("lib.injection_detector")
    fake_mod.InjectionDetector = _FakeDetector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lib.injection_detector", fake_mod)


def _patch_paths(monkeypatch, briefings_dir: Path, tmp_dir: Path) -> None:
    """Redirect briefing_archive path constants to tmp_path-scoped directories."""
    import lib.briefing_archive as ba

    monkeypatch.setattr(ba, "_BRIEFINGS_DIR", briefings_dir)
    monkeypatch.setattr(ba, "_TMP_DIR", tmp_dir)
    # Redirect audit/health paths to safe throwaway locations
    monkeypatch.setattr(ba, "_AUDIT_LOG", tmp_dir / "audit.md")
    monkeypatch.setattr(ba, "_HEALTH_CHECK", tmp_dir / "health-check.md")
    monkeypatch.setattr(ba, "_DRAFT_PATH", tmp_dir / "briefing_draft.md")


# ---------------------------------------------------------------------------
# T-RB-01: save() with runtime= stores runtime: in frontmatter
# ---------------------------------------------------------------------------

def test_save_with_runtime_adds_field(tmp_path, monkeypatch):
    """save() with runtime='gemini' writes runtime: gemini to frontmatter."""
    briefings_dir = tmp_path / "briefings"
    briefings_dir.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()

    _stub_injection_detector(monkeypatch, detected=False)
    _patch_paths(monkeypatch, briefings_dir, tmp_dir)

    # Reload to pick up patched constants
    import lib.briefing_archive as ba
    importlib.reload(ba)
    _patch_paths(monkeypatch, briefings_dir, tmp_dir)
    _stub_injection_detector(monkeypatch, detected=False)

    result = ba.save(
        "Gemini catch-up content.",
        source="interactive_cli",
        runtime="gemini",
    )

    assert result["status"] == "ok", f"Expected ok, got: {result}"

    today = date.today().strftime("%Y-%m-%d")
    written = (briefings_dir / f"{today}.md").read_text(encoding="utf-8")
    assert "runtime: gemini" in written
    assert "source: interactive_cli" in written


def test_save_without_runtime_omits_field(tmp_path, monkeypatch):
    """save() without runtime= does not write runtime: to frontmatter (backward compat)."""
    briefings_dir = tmp_path / "briefings"
    briefings_dir.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()

    _stub_injection_detector(monkeypatch, detected=False)
    _patch_paths(monkeypatch, briefings_dir, tmp_dir)

    import lib.briefing_archive as ba
    importlib.reload(ba)
    _patch_paths(monkeypatch, briefings_dir, tmp_dir)
    _stub_injection_detector(monkeypatch, detected=False)

    result = ba.save(
        "Telegram briefing content.",
        source="telegram",
        # runtime intentionally omitted
    )

    assert result["status"] == "ok"

    today = date.today().strftime("%Y-%m-%d")
    written = (briefings_dir / f"{today}.md").read_text(encoding="utf-8")
    assert "runtime:" not in written


# ---------------------------------------------------------------------------
# T-RB-02: _run_injection_check() raises RuntimeError if detector import fails
# ---------------------------------------------------------------------------

def test_injection_check_fail_closed_on_import_error(monkeypatch):
    """_run_injection_check() raises RuntimeError when detector module is missing."""
    # Remove any real or cached injection_detector from sys.modules
    monkeypatch.setitem(sys.modules, "lib.injection_detector", None)

    import lib.briefing_archive as ba
    importlib.reload(ba)

    with pytest.raises(RuntimeError, match="Injection detector unavailable"):
        ba._run_injection_check("any text")


def test_injection_check_fail_closed_on_scan_exception(monkeypatch):
    """_run_injection_check() raises RuntimeError when detector.scan() raises."""

    class _BrokenDetector:
        def scan(self, _text: str):
            raise ValueError("scan exploded")

    fake_mod = types.ModuleType("lib.injection_detector")
    fake_mod.InjectionDetector = _BrokenDetector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lib.injection_detector", fake_mod)

    import lib.briefing_archive as ba
    importlib.reload(ba)

    with pytest.raises(RuntimeError, match="Injection check failed"):
        ba._run_injection_check("any text")
