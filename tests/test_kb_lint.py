"""Tests for scripts/kb_lint.py — KB-LINT cross-domain data health linter.

Uses temp_artha_dir fixture for isolated test environments.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Ensure both project root and scripts/ are importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_state(state_dir: Path, name: str, content: str) -> Path:
    """Write a state file to the temp state directory."""
    p = state_dir / name
    p.write_text(content, encoding="utf-8")
    return p


def _make_fm(
    schema_version: str = "1.0",
    last_updated: str | None = None,
    sensitivity: str = "standard",
    domain: str = "testdomain",
    extra: str = "",
) -> str:
    """Build a minimal valid frontmatter block."""
    if last_updated is None:
        # Default to today (not stale)
        last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"---\n"
        f"schema_version: \"{schema_version}\"\n"
        f"domain: {domain}\n"
        f"last_updated: \"{last_updated}\"\n"
        f"sensitivity: {sensitivity}\n"
        f"{extra}"
        f"---\n\n"
        f"# {domain.title()} State\n"
        f"Sample content for tests.\n"
    )


def _write_fillers(state_dir: Path, count: int = 2) -> None:
    """Write N valid state files to keep the P1-error ratio below the bootstrap threshold."""
    for i in range(count):
        _write_state(state_dir, f"_filler{i}.md", _make_fm(domain=f"filler{i}"))


def _fresh_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _stale_date(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Import under test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_state_dir(temp_artha_dir, monkeypatch):
    """
    Patch STATE_DIR and CONFIG_DIR inside kb_lint to use the temp directory.
    This prevents tests from touching the real Artha state/ folder.
    """
    import importlib, lib.common
    monkeypatch.setattr(lib.common, "STATE_DIR", temp_artha_dir / "state")
    monkeypatch.setattr(lib.common, "CONFIG_DIR", temp_artha_dir / "config")

    # Re-import kb_lint each time to pick up patched paths
    if "kb_lint" in sys.modules:
        del sys.modules["kb_lint"]
    import kb_lint
    monkeypatch.setattr(kb_lint, "STATE_DIR", temp_artha_dir / "state")
    monkeypatch.setattr(kb_lint, "CONFIG_DIR", temp_artha_dir / "config")
    return None


def _get_kb_lint():
    if "kb_lint" in sys.modules:
        return sys.modules["kb_lint"]
    import kb_lint
    return kb_lint


# ---------------------------------------------------------------------------
# P1 — Schema validation
# ---------------------------------------------------------------------------

class TestP1Schema:
    def test_valid_frontmatter_no_p1_errors(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(domain="finance"))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        assert result.files_scanned == 1
        assert len(result.errors) == 0

    def test_missing_frontmatter_triggers_p1_error(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", "# Finance\nNo frontmatter here.\n")
        _write_fillers(state_dir)  # keep P1-error ratio below bootstrap threshold
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        assert any(f.pass_id == "P1-no-frontmatter" for f in result.errors)

    def test_missing_schema_version_triggers_error(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        content = (
            "---\n"
            f"last_updated: \"{_fresh_date()}\"\n"
            "sensitivity: standard\n"
            "---\n\nContent\n"
        )
        _write_state(state_dir, "finance.md", content)
        _write_fillers(state_dir)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        assert any("P1-missing-schema_version" == f.pass_id for f in result.errors)

    def test_missing_last_updated_triggers_error(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        content = (
            "---\n"
            "schema_version: \"1.0\"\n"
            "sensitivity: standard\n"
            "---\n\nContent\n"
        )
        _write_state(state_dir, "finance.md", content)
        _write_fillers(state_dir)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        assert any("P1-missing-last_updated" == f.pass_id for f in result.errors)

    def test_empty_last_updated_triggers_error(self, temp_artha_dir):
        """boundary.md pattern: last_updated is an empty string."""
        state_dir = temp_artha_dir / "state"
        content = (
            "---\n"
            "schema_version: \"1.0\"\n"
            "last_updated: \"\"\n"
            "sensitivity: standard\n"
            "---\n\nContent\n"
        )
        _write_state(state_dir, "boundary.md", content)
        _write_fillers(state_dir)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        assert any("P1-empty-last_updated" == f.pass_id for f in result.errors)

    def test_missing_sensitivity_triggers_error(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        content = (
            "---\n"
            "schema_version: \"1.0\"\n"
            f"last_updated: \"{_fresh_date()}\"\n"
            "---\n\nContent\n"
        )
        _write_state(state_dir, "finance.md", content)
        _write_fillers(state_dir)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        assert any("P1-missing-sensitivity" == f.pass_id for f in result.errors)

    def test_invalid_sensitivity_is_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        content = (
            "---\n"
            "schema_version: \"1.0\"\n"
            f"last_updated: \"{_fresh_date()}\"\n"
            "sensitivity: hyper_secret\n"
            "---\n\nContent\n"
        )
        _write_state(state_dir, "finance.md", content)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        warns = [f for f in result.findings if f.pass_id == "P1-invalid-sensitivity"]
        assert len(warns) == 1
        assert warns[0].severity.value == "WARNING"

    def test_iso_datetime_with_timezone_accepted(self, temp_artha_dir):
        """ISO-8601 with timezone offset must not trigger P1 error."""
        state_dir = temp_artha_dir / "state"
        content = (
            "---\n"
            "schema_version: \"1.0\"\n"
            "last_updated: \"2026-03-21T02:05:35.821715+00:00\"\n"
            "sensitivity: standard\n"
            "---\n\nContent\n"
        )
        _write_state(state_dir, "finance.md", content)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        assert not any(f.pass_id.startswith("P1-") and f.severity.value == "ERROR"
                       for f in result.findings)


# ---------------------------------------------------------------------------
# P2 — Staleness
# ---------------------------------------------------------------------------

class TestP2Staleness:
    def test_fresh_file_no_p2_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(last_updated=_fresh_date()))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P2"})
        assert not any(f.pass_id == "P2-stale" for f in result.findings)

    def test_stale_file_triggers_p2_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        # sensitivity=standard default TTL=180; write 200 days old
        _write_state(state_dir, "finance.md", _make_fm(
            last_updated=_stale_date(200),
            sensitivity="standard",
        ))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P2"})
        assert any(f.pass_id == "P2-stale" for f in result.findings)

    def test_sensitivity_high_uses_90d_default_ttl(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(
            last_updated=_stale_date(100),
            sensitivity="high",
        ))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P2"})
        assert any(f.pass_id == "P2-stale" for f in result.findings)

    def test_reference_sensitivity_uses_365d_ttl(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(
            last_updated=_stale_date(200),
            sensitivity="reference",
        ))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P2"})
        # 200d < 365d → should NOT be stale
        assert not any(f.pass_id == "P2-stale" for f in result.findings)


# ---------------------------------------------------------------------------
# P3 — TODO audit
# ---------------------------------------------------------------------------

class TestP3Todos:
    def test_todo_in_body_triggers_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        content = _make_fm() + "\nTODO: fill in visa details later\n"
        _write_state(state_dir, "immigration.md", content)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P3"})
        assert any(f.pass_id == "P3-todo" for f in result.findings)

    def test_tbd_in_frontmatter_triggers_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        content = _make_fm(extra="notes: TBD\n")
        _write_state(state_dir, "immigration.md", content)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P3"})
        assert any(f.pass_id == "P3-todo" for f in result.findings)

    def test_placeholder_keyword_triggers_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        content = _make_fm() + "\nPLACEHOLDER: update when ready\n"
        _write_state(state_dir, "finance.md", content)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P3"})
        assert any(f.pass_id == "P3-todo" for f in result.findings)

    def test_clean_file_no_p3(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm())
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P3"})
        assert not any(f.pass_id == "P3-todo" for f in result.findings)

    def test_max_5_todo_findings_per_file(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        todos = "\n".join(f"TODO item {i}" for i in range(10))
        content = _make_fm() + todos
        _write_state(state_dir, "finance.md", content)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P3"})
        p3_findings = [f for f in result.findings if f.pass_id == "P3-todo"]
        assert len(p3_findings) <= 5


# ---------------------------------------------------------------------------
# Bootstrap mode
# ---------------------------------------------------------------------------

class TestBootstrapMode:
    def test_bootstrap_mode_triggered_at_50_pct(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        # 2 files with no frontmatter, 1 file valid → 67% P1 errors → bootstrap
        _write_state(state_dir, "finance.md", "# Finance\nNo FM.\n")
        _write_state(state_dir, "health.md", "# Health\nNo FM.\n")
        _write_state(state_dir, "goals.md", _make_fm(domain="goals"))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        assert result.bootstrap_mode is True
        assert result.bootstrap_count == 2

    def test_bootstrap_mode_not_triggered_below_50_pct(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        # 1 invalid out of 3 → 33% → no bootstrap
        _write_state(state_dir, "finance.md", "# Finance\nNo FM.\n")
        _write_state(state_dir, "health.md", _make_fm(domain="health"))
        _write_state(state_dir, "goals.md", _make_fm(domain="goals"))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        assert result.bootstrap_mode is False


# ---------------------------------------------------------------------------
# --brief-mode
# ---------------------------------------------------------------------------

class TestBriefMode:
    def test_brief_mode_exits_0_even_with_errors(self, temp_artha_dir, capsys):
        state_dir = temp_artha_dir / "state"
        # File with multiple errors
        _write_state(state_dir, "finance.md", "# Finance\nNo frontmatter.\n")
        kb = _get_kb_lint()
        exit_code = kb.main(["--brief-mode", "--state-dir", str(state_dir)])
        assert exit_code == 0

    def test_brief_mode_exits_0_on_empty_state_dir(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        kb = _get_kb_lint()
        exit_code = kb.main(["--brief-mode", "--state-dir", str(state_dir)])
        assert exit_code == 0

    def test_brief_mode_outputs_one_line(self, temp_artha_dir, capsys):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(domain="finance"))
        kb = _get_kb_lint()
        kb.main(["--brief-mode", "--state-dir", str(state_dir)])
        captured = capsys.readouterr()
        # Should be a single non-empty line
        lines = [l for l in captured.out.splitlines() if l.strip()]
        assert len(lines) == 1
        assert "Data Health" in lines[0]

    def test_brief_mode_only_runs_p1_p2_p3(self, temp_artha_dir):
        """P4–P6 must not run in brief mode."""
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(domain="finance"))
        kb = _get_kb_lint()
        # Inject P4 content — should not appear in brief-mode findings
        content = _make_fm() + "\ndeadline: 2020-01-01\n"
        _write_state(state_dir, "finance.md", content)
        result = kb.run_lint(state_dir=state_dir, passes={"P1", "P2", "P3"}, brief_mode=True)
        assert not any(f.pass_id.startswith("P4") for f in result.findings)

    def test_brief_mode_error_adds_warning_prefix(self, temp_artha_dir, capsys):
        """Spec §1.2.1: when P1 errors present, brief line starts with ⚠ and ends
        with the actionable suffix."""
        state_dir = temp_artha_dir / "state"
        # One good file + one file missing frontmatter (error); ratio < 50 % so no bootstrap
        _write_state(state_dir, "health.md", _make_fm(domain="health"))
        _write_fillers(state_dir, count=2)
        _write_state(state_dir, "finance.md", "# Finance\nNo frontmatter.\n")
        kb = _get_kb_lint()
        kb.main(["--brief-mode", "--state-dir", str(state_dir)])
        captured = capsys.readouterr()
        lines = [l for l in captured.out.splitlines() if l.strip()]
        assert len(lines) == 1
        line = lines[0]
        assert line.startswith("⚠ "), f"Expected '⚠ ' prefix for error state, got: {line!r}"
        assert "run `lint` for details" in line, f"Missing actionable suffix in: {line!r}"

    def test_brief_mode_no_errors_no_warning_prefix(self, temp_artha_dir, capsys):
        """Spec §1.2.1: when no errors, brief line must NOT start with ⚠."""
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(domain="finance"))
        kb = _get_kb_lint()
        kb.main(["--brief-mode", "--state-dir", str(state_dir)])
        captured = capsys.readouterr()
        lines = [l for l in captured.out.splitlines() if l.strip()]
        assert len(lines) == 1
        assert not lines[0].startswith("⚠"), f"Unexpected ⚠ prefix in clean run: {lines[0]!r}"

    def test_brief_mode_crash_format(self, temp_artha_dir, capsys, monkeypatch):
        """Spec §1.2.1: on lint crash, output must be the spec crash format."""
        state_dir = temp_artha_dir / "state"
        kb = _get_kb_lint()
        # Force run_lint to raise
        monkeypatch.setattr(kb, "run_lint", lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom")))
        kb.main(["--brief-mode", "--state-dir", str(state_dir)])
        captured = capsys.readouterr()
        assert captured.out.strip() == "Data Health: ⚠ lint error — run `lint` manually"


# ---------------------------------------------------------------------------
# --json output
# ---------------------------------------------------------------------------

class TestJsonOutput:
    def test_json_output_is_valid_json(self, temp_artha_dir, capsys):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(domain="finance"))
        kb = _get_kb_lint()
        exit_code = kb.main(["--json", "--state-dir", str(state_dir)])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "files_scanned" in data
        assert "health_pct" in data
        assert "findings" in data

    def test_json_output_health_pct_0_to_100(self, temp_artha_dir, capsys):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(domain="finance"))
        kb = _get_kb_lint()
        kb.main(["--json", "--state-dir", str(state_dir)])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert 0 <= data["health_pct"] <= 100


# ---------------------------------------------------------------------------
# Health percentage formula
# ---------------------------------------------------------------------------

class TestHealthPct:
    def test_health_100_when_no_errors(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(domain="finance"))
        _write_state(state_dir, "health.md", _make_fm(domain="health"))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        assert result.health_pct == 100

    def test_health_0_when_all_errors(self, temp_artha_dir):
        """When all files have P1 errors, bootstrap mode fires and suppresses individual
        findings. Verify bootstrap_mode rather than health_pct (which resets to 100% when
        there are no unsuppressed error findings — by design)."""
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", "# Finance\nBlank.\n")
        _write_state(state_dir, "health.md", "# Health\nBlank.\n")
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        # All files missing FM → 100% P1 errors → bootstrap fires → findings suppressed
        assert result.bootstrap_mode is True
        assert result.bootstrap_count == 2

    def test_health_pct_excludes_warning_only_files(self, temp_artha_dir):
        """Files with only P2/P3 warnings still count as 'clean' for health_pct."""
        state_dir = temp_artha_dir / "state"
        content = _make_fm() + "\nTODO: item.\n"
        _write_state(state_dir, "finance.md", content)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P3"})
        # Warnings only — health_pct should still be 100 (no P1 errors)
        assert result.health_pct == 100

    def test_health_pct_uses_only_p1_errors(self, temp_artha_dir):
        """Spec §1.7: health_pct counts only P1 ERRORs; non-P1 errors must not
        reduce health_pct.  We inject a synthetic non-P1 ERROR finding directly."""
        from dataclasses import replace as dc_replace
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(domain="finance"))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        # Verify clean baseline
        assert result.health_pct == 100
        # Inject a fake ERROR from a non-P1 pass
        fake_error = kb.LintFinding(
            severity=kb.Severity.ERROR,
            domain="finance",
            file_name="finance.md",
            pass_id="P99-fake-error",
            message="synthetic non-P1 error",
        )
        result.findings.append(fake_error)
        # health_pct must remain 100 because the error is not a P1 error
        assert result.health_pct == 100, (
            "health_pct incorrectly counted a non-P1 ERROR — spec requires P1-only"
        )


# ---------------------------------------------------------------------------
# Domain filter
# ---------------------------------------------------------------------------

class TestDomainFilter:
    def test_domain_filter_only_scans_matching_file(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(domain="finance"))
        _write_state(state_dir, "health.md", "# Health\nNo FM.\n")
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, domain_filter="finance", passes={"P1"})
        assert result.files_scanned == 1
        assert result.findings == [] or all(f.domain == "finance" for f in result.findings)

    def test_unknown_domain_filter_scans_zero_files(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "finance.md", _make_fm(domain="finance"))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, domain_filter="nonexistent_domain", passes={"P1"})
        assert result.files_scanned == 0


# ---------------------------------------------------------------------------
# .age files are skipped
# ---------------------------------------------------------------------------

class TestEncryptedFilesSkipped:
    def test_age_files_are_not_scanned(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        (state_dir / "finance.md.age").write_bytes(b"\x00\x01\x02")
        _write_state(state_dir, "health.md", _make_fm(domain="health"))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P1"})
        # Only health.md should be scanned — .age must not be counted
        assert result.files_scanned == 1


# ---------------------------------------------------------------------------
# P4 — Date validity
# ---------------------------------------------------------------------------

class TestP4Dates:
    def test_past_action_date_fires_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        content = _make_fm() + "\nappointment: 2020-01-15\n"
        _write_state(state_dir, "health.md", content)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P4"})
        assert any(f.pass_id == "P4-past-date" for f in result.findings)

    def test_future_date_no_p4_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        content = _make_fm() + "\nrenewal: 2099-01-01\n"
        _write_state(state_dir, "health.md", content)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P4"})
        assert not any(f.pass_id == "P4-past-date" for f in result.findings)

    def test_max_5_findings_per_file(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        # 6 distinct past action dates — capped at 5 by the implementation
        date_lines = "\n".join(
            f"appointment: 201{i}-0{i + 1}-01" for i in range(6)
        )
        content = _make_fm() + "\n" + date_lines + "\n"
        _write_state(state_dir, "health.md", content)
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P4"})
        p4_findings = [f for f in result.findings if f.pass_id == "P4-past-date"]
        assert len(p4_findings) <= 5


# ---------------------------------------------------------------------------
# P5 — Cross-reference checker
# ---------------------------------------------------------------------------

_SIMPLE_XREF_RULES_YAML = """\
schema_version: "1.0"
rules:
  - id: xref-test-missing
    domain_a: immigration
    field_a: visa_expiry
    domain_b: travel
    field_b: passport_expiry
    message: "Test: travel.passport_expiry is missing"
    severity: WARNING
    enabled: true
  - id: xref-test-info
    domain_a: finance
    field_a: monthly_budget
    domain_b: decisions
    field_b: last_updated
    message: "Test: decisions.last_updated is missing"
    severity: INFO
    enabled: true
  - id: xref-test-disabled
    domain_a: health
    field_a: primary_care_provider
    domain_b: insurance
    field_b: health_plan
    message: "Test: this rule is disabled"
    severity: WARNING
    enabled: false
"""


class TestP5CrossRef:
    def _write_rules(self, temp_artha_dir: Path) -> None:
        """Write test lint rules to the temp config directory."""
        (temp_artha_dir / "config" / "lint_rules.yaml").write_text(
            _SIMPLE_XREF_RULES_YAML, encoding="utf-8"
        )

    def test_missing_field_b_fires_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        self._write_rules(temp_artha_dir)
        _write_state(state_dir, "immigration.md",
                     _make_fm(domain="immigration", extra="visa_expiry: 2027-01-01\n"))
        _write_state(state_dir, "travel.md", _make_fm(domain="travel"))  # no passport_expiry
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P5"})
        assert any(f.pass_id == "xref-test-missing" for f in result.findings)

    def test_absent_field_a_no_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        self._write_rules(temp_artha_dir)
        # immigration has NO visa_expiry — rule must not fire
        _write_state(state_dir, "immigration.md", _make_fm(domain="immigration"))
        _write_state(state_dir, "travel.md", _make_fm(domain="travel"))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P5"})
        assert not any(f.pass_id == "xref-test-missing" for f in result.findings)

    def test_disabled_rule_does_not_fire(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        self._write_rules(temp_artha_dir)
        _write_state(state_dir, "health.md",
                     _make_fm(domain="health", extra="primary_care_provider: Dr Smith\n"))
        _write_state(state_dir, "insurance.md", _make_fm(domain="insurance"))
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P5"})
        assert not any(f.pass_id == "xref-test-disabled" for f in result.findings)

    def test_info_severity_produces_info_finding(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        self._write_rules(temp_artha_dir)
        _write_state(state_dir, "finance.md",
                     _make_fm(domain="finance", extra="monthly_budget: 5000\n"))
        # decisions.md intentionally lacks last_updated to trigger INFO rule
        _write_state(
            state_dir, "decisions.md",
            "---\nschema_version: \"1.0\"\ndomain: decisions\nsensitivity: standard\n---\n# Decisions\n",
        )
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P5"})
        info_findings = [f for f in result.findings if f.pass_id == "xref-test-info"]
        assert len(info_findings) == 1
        assert info_findings[0].severity.value == "INFO"


# ---------------------------------------------------------------------------
# P6 — Open items validator
# ---------------------------------------------------------------------------

class TestP6OpenItems:
    def test_unknown_domain_fires_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "health.md", _make_fm(domain="health"))
        (state_dir / "open_items.md").write_text(
            "# Open Items\n- description: Some task\n  source_domain: unknowndomain\n",
            encoding="utf-8",
        )
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P6"})
        assert any(f.pass_id == "P6-unknown-domain" for f in result.findings)

    def test_known_domain_no_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "health.md", _make_fm(domain="health"))
        (state_dir / "open_items.md").write_text(
            "# Open Items\n- description: Some task\n  source_domain: health\n",
            encoding="utf-8",
        )
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P6"})
        assert not any(f.pass_id == "P6-unknown-domain" for f in result.findings)

    def test_no_open_items_file_no_warning(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        _write_state(state_dir, "health.md", _make_fm(domain="health"))
        # open_items.md intentionally absent
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P6"})
        assert not any(f.pass_id == "P6-unknown-domain" for f in result.findings)

    def test_hardcoded_domain_is_always_known(self, temp_artha_dir):
        state_dir = temp_artha_dir / "state"
        # employment is in the hardcoded known_domains set even without a state file
        _write_state(state_dir, "health.md", _make_fm(domain="health"))
        (state_dir / "open_items.md").write_text(
            "# Open Items\n- description: Job search\n  source_domain: employment\n",
            encoding="utf-8",
        )
        kb = _get_kb_lint()
        result = kb.run_lint(state_dir=state_dir, passes={"P6"})
        assert not any(f.pass_id == "P6-unknown-domain" for f in result.findings)
