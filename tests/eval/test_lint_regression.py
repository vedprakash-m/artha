"""tests/eval/test_lint_regression.py — KB lint eval-layer regression harness.

Synthetic fixtures only — no real user PII (DD-5).
Covers spec: specs/kb.md §1.6 L8 "Eval integration: add lint scenarios
to tests/eval/ regression harness."

Scenarios
---------
1. Golden — all-clean state dir produces 0 P1 errors.
2. Anti-golden — missing-frontmatter files are flagged by P1.
3. Stale file — P2 staleness warning fires when updated_at is old.
4. Fix-mode WriteGuard block — _apply_fix returns 'blocked' when middleware
   signals >20% field loss.
5. Fix-mode post-fix regression — _apply_fix returns 'verify_failed' when
   _atomic_write_with_verify signals P1 failure on temp file.
6. Regression sentinel — lint finding count on a stable clean fixture
   never increases between two successive runs.
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"

for _p in (_PROJECT_ROOT, _SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# Module loader — load kb_lint once; reuse across tests in this session
# ---------------------------------------------------------------------------
_kb_lint_mod = None


def _load_kb_lint():
    global _kb_lint_mod
    if _kb_lint_mod is None:
        spec = importlib.util.spec_from_file_location(
            "kb_lint_eval", _SCRIPTS_DIR / "kb_lint.py"
        )
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules["kb_lint_eval"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _kb_lint_mod = mod
    return _kb_lint_mod


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

# Required fields: schema_version, last_updated, sensitivity
_VALID_FRONTMATTER_TMPL = textwrap.dedent("""\
    ---
    schema_version: "1.0"
    last_updated: 2026-01-15
    sensitivity: standard
    ---
    # {domain} State — synthetic fixture
    Nothing of consequence.
""")

_VALID_FRONTMATTER = _VALID_FRONTMATTER_TMPL.format(domain="finance")

_NO_FRONTMATTER = textwrap.dedent("""\
    # Finance State — missing frontmatter
    This file has no YAML front matter at all.
""")

_STALE_FRONTMATTER = textwrap.dedent("""\
    ---
    schema_version: "1.0"
    last_updated: 2020-01-01
    sensitivity: standard
    ---
    # Finance State — stale file
    Last touched years ago.
""")


def _write_state_file(state_dir: Path, filename: str, content: str) -> Path:
    path = state_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def _run(state_dir: Path, **kwargs):
    """Call run_lint with a synthetic state dir."""
    kb = _load_kb_lint()
    return kb.run_lint(state_dir=state_dir, **kwargs)


# ---------------------------------------------------------------------------
# §1.6 L8 Scenario 1 — Golden: all-clean state produces 0 P1 errors
# ---------------------------------------------------------------------------


class TestGoldenCleanState:
    """All-clean synthetic domain files should produce zero P1 ERROR findings."""

    def test_clean_frontmatter_no_p1_errors(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        _write_state_file(state_dir, "finance.md", _VALID_FRONTMATTER)

        result = _run(state_dir)

        p1_errors = [
            f for f in result.findings
            if f.severity.value == "ERROR" and f.file_name == "finance.md"
        ]
        assert p1_errors == [], (
            f"Expected 0 P1 errors on clean fixture but got: {p1_errors}"
        )

    def test_multiple_clean_files_no_p1_errors(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        for domain in ("finance", "health", "home"):
            _write_state_file(
                state_dir, f"{domain}.md",
                _VALID_FRONTMATTER_TMPL.format(domain=domain)
            )

        result = _run(state_dir)

        p1_errors = [f for f in result.findings if f.severity.value == "ERROR"]
        assert p1_errors == [], f"Clean multi-file state produced P1 errors: {p1_errors}"


# ---------------------------------------------------------------------------
# §1.6 L8 Scenario 2 — Anti-golden: missing frontmatter is flagged
# ---------------------------------------------------------------------------


class TestAntiGoldenMissingFrontmatter:
    """Files missing YAML front matter must produce P1 ERROR findings.

    Bootstrap mode suppresses individual P1 errors when ≥50% of files fail P1.
    These tests use 3-file state dirs (1 bad + 2 good) to stay below the 50%
    threshold, ensuring individual P1 findings are reported.
    """

    def test_missing_frontmatter_produces_p1_error(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        # 1 bad + 2 good → 33% error rate; below 50% bootstrap threshold
        _write_state_file(state_dir, "finance.md", _NO_FRONTMATTER)
        _write_state_file(state_dir, "health.md", _VALID_FRONTMATTER_TMPL.format(domain="health"))
        _write_state_file(state_dir, "home.md", _VALID_FRONTMATTER_TMPL.format(domain="home"))

        result = _run(state_dir)

        p1_errors = [
            f for f in result.findings
            if f.severity.value == "ERROR" and f.file_name == "finance.md"
        ]
        assert len(p1_errors) >= 1, (
            "Expected at least one P1 ERROR for missing-frontmatter file, got none. "
            f"All findings: {[(f.file_name, f.pass_id, f.severity.value) for f in result.findings]}"
        )

    def test_missing_frontmatter_finding_is_fixable(self, tmp_path):
        """P1 missing-frontmatter findings must expose a fix action."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        _write_state_file(state_dir, "finance.md", _NO_FRONTMATTER)
        _write_state_file(state_dir, "health.md", _VALID_FRONTMATTER_TMPL.format(domain="health"))
        _write_state_file(state_dir, "home.md", _VALID_FRONTMATTER_TMPL.format(domain="home"))

        result = _run(state_dir)

        p1_fixable = [
            f for f in result.findings
            if f.severity.value == "ERROR"
            and f.file_name == "finance.md"
            and f.fixable
        ]
        assert len(p1_fixable) >= 1, (
            "Expected the P1 missing-frontmatter finding to be marked fixable."
        )


# ---------------------------------------------------------------------------
# §1.6 L8 Scenario 3 — P2 staleness detected
# ---------------------------------------------------------------------------


class TestStalenessWarning:
    """Files with very old last_updated must produce a P2-stale WARNING finding."""

    def test_stale_file_produces_p2_warning(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        _write_state_file(state_dir, "finance.md", _STALE_FRONTMATTER)

        result = _run(state_dir)

        p2_findings = [
            f for f in result.findings
            if f.file_name == "finance.md" and f.pass_id == "P2-stale"
        ]
        # A file last edited in 2020 must exceed any staleness TTL.
        assert p2_findings, (
            "Expected at least one P2-stale finding for a file last updated on "
            "2020-01-01, but lint reported none. "
            f"All findings: {[(f.file_name, f.pass_id, f.severity.value) for f in result.findings]}"
        )


# ---------------------------------------------------------------------------
# §1.6 L8 Scenario 4 — WriteGuard block path
# ---------------------------------------------------------------------------


class TestApplyFixWriteGuardBlock:
    """_apply_fix must return 'blocked' when WriteGuardMiddleware returns None."""

    def test_write_guard_block_returns_blocked(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        path = _write_state_file(state_dir, "finance.md", _NO_FRONTMATTER)

        kb = _load_kb_lint()

        # LintFinding field order: severity, domain, file_name, pass_id, message
        finding = kb.LintFinding(
            severity=kb.Severity.ERROR,
            domain="finance",
            file_name="finance.md",
            pass_id="P1",
            message="Missing frontmatter",
            fixable=True,
            fix_description="Add skeleton frontmatter",
            fix_data={"action": "add_frontmatter_skeleton"},
        )
        registry: dict[str, Any] = {"finance": {"sensitivity": "standard"}}

        # Patch WriteGuardMiddleware.before_write to simulate a block (returns None)
        mock_guard = MagicMock()
        mock_guard.before_write.return_value = None

        mock_wg_module = MagicMock()
        mock_wg_module.WriteGuardMiddleware.return_value = mock_guard

        with patch.dict(sys.modules, {"middleware.write_guard": mock_wg_module}):
            status = kb._apply_fix(finding, state_dir, registry)

        assert status == "blocked", (
            f"Expected 'blocked' when WriteGuard returns None, got '{status}'"
        )
        # Original file must be untouched
        assert path.read_text(encoding="utf-8") == _NO_FRONTMATTER, (
            "WriteGuard-blocked fix must not modify the original file."
        )


# ---------------------------------------------------------------------------
# §1.6 L8 Scenario 5 — Post-fix regression path (verify_failed)
# ---------------------------------------------------------------------------


class TestApplyFixVerifyFailed:
    """_apply_fix must return 'verify_failed' when _atomic_write_with_verify does."""

    def test_verify_failed_leaves_original_intact(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        path = _write_state_file(state_dir, "finance.md", _NO_FRONTMATTER)
        original_content = path.read_text(encoding="utf-8")

        kb = _load_kb_lint()

        finding = kb.LintFinding(
            severity=kb.Severity.ERROR,
            domain="finance",
            file_name="finance.md",
            pass_id="P1",
            message="Missing frontmatter",
            fixable=True,
            fix_description="Add skeleton frontmatter",
            fix_data={"action": "add_frontmatter_skeleton"},
        )
        registry: dict[str, Any] = {"finance": {"sensitivity": "standard"}}

        # Patch WriteGuardMiddleware to allow (pass-through) and
        # _atomic_write_with_verify to simulate a P1 regression on the temp file.
        mock_guard = MagicMock()
        mock_guard.before_write.side_effect = lambda d, c, p: p  # allow

        mock_wg_module = MagicMock()
        mock_wg_module.WriteGuardMiddleware.return_value = mock_guard

        with patch.dict(sys.modules, {"middleware.write_guard": mock_wg_module}):
            with patch.object(kb, "_atomic_write_with_verify", return_value="verify_failed"):
                status = kb._apply_fix(finding, state_dir, registry)

        assert status == "verify_failed", (
            f"Expected 'verify_failed' status, got '{status}'"
        )
        # Original file must still be untouched (rename never happened)
        assert path.read_text(encoding="utf-8") == original_content, (
            "verify_failed path must leave the original file untouched."
        )


# ---------------------------------------------------------------------------
# §1.6 L8 Scenario 6 — Regression sentinel: re-lint same clean dir → same count
# ---------------------------------------------------------------------------


class TestRegressionSentinel:
    """Running lint twice on the same clean directory must yield identical results."""

    def test_deterministic_clean_run(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        for domain in ("finance", "health"):
            _write_state_file(
                state_dir, f"{domain}.md",
                _VALID_FRONTMATTER_TMPL.format(domain=domain)
            )

        result_a = _run(state_dir)
        result_b = _run(state_dir)

        findings_a = sorted(
            (f.file_name, f.pass_id, f.severity.value) for f in result_a.findings
        )
        findings_b = sorted(
            (f.file_name, f.pass_id, f.severity.value) for f in result_b.findings
        )

        assert findings_a == findings_b, (
            "run_lint is non-deterministic: successive runs on an unmodified "
            f"directory produced different findings.\n  Run A: {findings_a}\n  Run B: {findings_b}"
        )

    def test_finding_count_does_not_regress_across_runs(self, tmp_path):
        """Error count on a stable state dir must never increase between runs."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        _write_state_file(state_dir, "finance.md", _VALID_FRONTMATTER)

        first_error_count = sum(
            1 for f in _run(state_dir).findings if f.severity.value == "ERROR"
        )
        second_error_count = sum(
            1 for f in _run(state_dir).findings if f.severity.value == "ERROR"
        )

        assert second_error_count <= first_error_count, (
            f"Error count increased from {first_error_count} to {second_error_count} "
            "on an unmodified state directory (regression detected)."
        )
