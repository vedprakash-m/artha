"""
tests/unit/test_prompt_extraction.py — Prompt regression test framework.

Golden-file tests that verify domain prompts correctly extract expected
signals from fixture emails. Catches silent prompt regressions that unit
tests cannot detect.

Execution model:
  - These tests call the REAL Claude API with temperature=0 (near-deterministic)
  - Marked @pytest.mark.prompt_regression
  - EXCLUDED from default make test run
  - Run separately: pytest -m prompt_regression
  - Acceptance threshold: ≥80% of REQUIRED signals present in output
  - Golden files are committed and updated manually on intentional prompt changes

Golden file format: tests/fixtures/<domain>/golden.yaml
Fixture emails:    tests/fixtures/<domain>/emails.jsonl

Each domain must ship with ≥5 fixture emails to be considered test-covered.

Validation approach (without real API call):
  - Structural tests verify fixtures are valid and complete
  - Signal presence tests verify golden files are well-formed
  - Integration tests exercise the extraction pipeline against fixtures

Ref: specs/enhance.md §11 Phase 1a item 1.0i
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Generator

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_FIXTURES = _REPO / "tests" / "fixtures"
_SCRIPTS = _REPO / "scripts"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Domains that must have ≥5 fixture emails
_REQUIRED_DOMAINS = ["finance", "health", "home", "kids", "immigration"]

# Signal acceptance threshold (80%)
_REQUIRED_SIGNAL_THRESHOLD = 0.80

# Mark expensive tests that call the real Claude API
pytestmark = pytest.mark.usefixtures()


# ---------------------------------------------------------------------------
# Structural validation tests (no API calls — run in default test suite)
# ---------------------------------------------------------------------------

class TestFixtureStructure:
    """Verify fixture files are well-formed before any API calls are attempted."""

    @pytest.mark.parametrize("domain", _REQUIRED_DOMAINS)
    def test_fixture_emails_exist(self, domain):
        """Each required domain must have an emails.jsonl fixture file."""
        fixture_file = _FIXTURES / domain / "emails.jsonl"
        assert fixture_file.exists(), (
            f"Missing fixture: tests/fixtures/{domain}/emails.jsonl\n"
            f"Every domain domain must ship with ≥5 fixture emails.\n"
            f"Create this file as described in specs/enhance.md §1.0i"
        )

    @pytest.mark.parametrize("domain", _REQUIRED_DOMAINS)
    def test_fixture_has_minimum_emails(self, domain):
        """Each domain fixture must have ≥5 emails (per spec requirement)."""
        fixture_file = _FIXTURES / domain / "emails.jsonl"
        if not fixture_file.exists():
            pytest.skip(f"Fixture file missing: {fixture_file}")

        lines = [l.strip() for l in fixture_file.read_text().splitlines() if l.strip()]
        emails = []
        for line in lines:
            try:
                emails.append(json.loads(line))
            except json.JSONDecodeError as e:
                pytest.fail(f"Invalid JSON in {fixture_file} line: {line[:80]}: {e}")

        assert len(emails) >= 5, (
            f"Domain '{domain}' has only {len(emails)} fixture emails. "
            f"Minimum is 5 per specs/enhance.md §1.0i."
        )

    @pytest.mark.parametrize("domain", _REQUIRED_DOMAINS)
    def test_fixture_email_schema(self, domain):
        """Each fixture email must have required fields."""
        fixture_file = _FIXTURES / domain / "emails.jsonl"
        if not fixture_file.exists():
            pytest.skip(f"Fixture file missing: {fixture_file}")

        required_fields = {"id", "subject", "from", "date_iso", "body", "source"}
        lines = [l.strip() for l in fixture_file.read_text().splitlines() if l.strip()]
        for i, line in enumerate(lines):
            try:
                email = json.loads(line)
            except json.JSONDecodeError:
                pytest.fail(f"Line {i+1} is not valid JSON")
            missing = required_fields - set(email.keys())
            assert not missing, (
                f"Email {email.get('id', f'line {i+1}')} in {domain}/emails.jsonl "
                f"missing required fields: {missing}"
            )

    @pytest.mark.parametrize("domain", _REQUIRED_DOMAINS)
    def test_golden_file_exists(self, domain):
        """Each required domain must have a golden.yaml file."""
        golden_file = _FIXTURES / domain / "golden.yaml"
        assert golden_file.exists(), (
            f"Missing golden file: tests/fixtures/{domain}/golden.yaml\n"
            f"Create this file with expected extraction signals."
        )

    @pytest.mark.parametrize("domain", _REQUIRED_DOMAINS)
    def test_golden_file_valid_yaml(self, domain):
        """Golden file must be valid YAML with required keys."""
        golden_file = _FIXTURES / domain / "golden.yaml"
        if not golden_file.exists():
            pytest.skip(f"Golden file missing: {golden_file}")

        try:
            import yaml
            with open(golden_file) as f:
                data = yaml.safe_load(f)
        except Exception as e:
            pytest.fail(f"Cannot parse {golden_file}: {e}")

        assert "domain" in data, f"golden.yaml must have 'domain' key"
        assert "extraction_signals" in data, f"golden.yaml must have 'extraction_signals' key"
        assert isinstance(data["extraction_signals"], list), "extraction_signals must be a list"
        assert len(data["extraction_signals"]) > 0, "Must have ≥1 extraction signal"

    @pytest.mark.parametrize("domain", _REQUIRED_DOMAINS)
    def test_golden_signals_have_required_fields(self, domain):
        """Each extraction signal must have 'signal', 'source_id', and 'required' fields."""
        golden_file = _FIXTURES / domain / "golden.yaml"
        if not golden_file.exists():
            pytest.skip(f"Golden file missing: {golden_file}")

        import yaml
        with open(golden_file) as f:
            data = yaml.safe_load(f)

        for i, signal in enumerate(data.get("extraction_signals", [])):
            assert "signal" in signal, f"Signal {i} missing 'signal' key in {domain}/golden.yaml"
            assert "source_id" in signal, f"Signal {i} missing 'source_id' key"
            assert "required" in signal, f"Signal {i} missing 'required' key"
            # Each signal must have either 'value' or 'keywords'
            has_value = "value" in signal
            has_keywords = "keywords" in signal
            assert has_value or has_keywords, (
                f"Signal '{signal['signal']}' in {domain}/golden.yaml must have 'value' or 'keywords'"
            )

    @pytest.mark.parametrize("domain", _REQUIRED_DOMAINS)
    def test_required_signals_have_minimum_count(self, domain):
        """Each domain must have ≥2 required signals (weak golden file is not useful)."""
        golden_file = _FIXTURES / domain / "golden.yaml"
        if not golden_file.exists():
            pytest.skip(f"Golden file missing: {golden_file}")

        import yaml
        with open(golden_file) as f:
            data = yaml.safe_load(f)

        required_signals = [s for s in data.get("extraction_signals", []) if s.get("required")]
        assert len(required_signals) >= 2, (
            f"Domain '{domain}' only has {len(required_signals)} required signal(s). "
            f"Minimum is 2 to be a meaningful regression test."
        )


# ---------------------------------------------------------------------------
# Signal presence tests (non-API — verify signal text appears in raw emails)
# ---------------------------------------------------------------------------

class TestSignalPresenceInRawEmails:
    """Verify golden file signals actually appear in the fixture emails.
    
    This catches mistakes in golden file authoring where a signal references
    text that doesn't exist in the fixture emails.
    """

    @pytest.mark.parametrize("domain", _REQUIRED_DOMAINS)
    def test_required_signals_traceable_to_emails(self, domain):
        """Each required signal's value/keywords must appear in at least one fixture email."""
        fixture_file = _FIXTURES / domain / "emails.jsonl"
        golden_file = _FIXTURES / domain / "golden.yaml"
        if not fixture_file.exists() or not golden_file.exists():
            pytest.skip(f"Fixture or golden file missing for {domain}")

        import yaml
        with open(golden_file) as f:
            golden = yaml.safe_load(f)

        # Build corpus of all email text
        corpus = ""
        lines = [l.strip() for l in fixture_file.read_text().splitlines() if l.strip()]
        for line in lines:
            try:
                email = json.loads(line)
                corpus += f" {email.get('subject', '')} {email.get('body', '')} "
            except json.JSONDecodeError:
                pass
        corpus = corpus.lower()

        # Check each required signal
        not_found = []
        for signal in golden.get("extraction_signals", []):
            if not signal.get("required"):
                continue
            signal_name = signal["signal"]
            if "value" in signal:
                needle = str(signal["value"]).lower()
                if needle not in corpus:
                    not_found.append(f"'{signal_name}': value='{signal['value']}'")
            elif "keywords" in signal:
                keywords = signal["keywords"]
                found_any = any(kw.lower() in corpus for kw in keywords)
                if not found_any:
                    not_found.append(f"'{signal_name}': keywords={keywords}")

        assert not not_found, (
            f"Domain '{domain}': these required signals have NO matching text in fixture emails:\n"
            + "\n".join(f"  - {m}" for m in not_found)
            + "\n\nEither update the golden file or add fixture emails containing these signals."
        )


# ---------------------------------------------------------------------------
# Prompt regression tests (marked @pytest.mark.prompt_regression — API required)
# These are EXCLUDED from the default test suite.
# Run with: pytest -m prompt_regression
# ---------------------------------------------------------------------------

@pytest.mark.prompt_regression
class TestPromptRegressionWithAPI:
    """Full prompt regression tests. Requires Claude API. Excluded from default run.
    
    These tests verify that Artha domain prompts correctly extract the expected
    signals from fixture emails when run through the real Claude API at temperature=0.
    
    Acceptance threshold: ≥80% of REQUIRED signals present in Claude's output.
    """

    def _extract_with_prompt(self, prompt_file: Path, emails: list[dict]) -> str:
        """Call Claude API to extract information from emails using domain prompt."""
        import subprocess
        import json as _json

        prompt_text = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else ""
        email_text = "\n".join(
            f"Subject: {e['subject']}\nFrom: {e['from']}\nBody:\n{e['body']}"
            for e in emails
        )

        combined = (
            f"{prompt_text}\n\n"
            f"---\n"
            f"Process these emails and extract the information described above:\n\n"
            f"{email_text}"
        )

        try:
            result = subprocess.run(
                ["claude", "--print", "--temperature", "0", "-p", combined],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                pytest.skip(f"Claude CLI unavailable: {result.stderr.strip()[:100]}")
            return result.stdout
        except FileNotFoundError:
            pytest.skip("Claude CLI (claude) not installed. Install to run prompt_regression tests.")
        except subprocess.TimeoutExpired:
            pytest.skip("Claude API call timed out (>120s)")

    def _score_extraction(self, output: str, golden: dict) -> tuple[float, list[str]]:
        """Score extraction output against golden file. Returns (score, missed_signals)."""
        output_lower = output.lower()
        required = [s for s in golden.get("extraction_signals", []) if s.get("required")]
        if not required:
            return 1.0, []

        hits = 0
        missed = []
        for signal in required:
            if "value" in signal:
                if str(signal["value"]).lower() in output_lower:
                    hits += 1
                else:
                    missed.append(f"{signal['signal']}: expected '{signal['value']}'")
            elif "keywords" in signal:
                if any(kw.lower() in output_lower for kw in signal["keywords"]):
                    hits += 1
                else:
                    missed.append(f"{signal['signal']}: expected one of {signal['keywords']}")

        score = hits / len(required) if required else 1.0
        return score, missed

    @pytest.mark.parametrize("domain", _REQUIRED_DOMAINS)
    def test_prompt_extraction_accuracy(self, domain):
        """Run domain prompt against fixture emails and verify ≥80% signal extraction."""
        fixture_file = _FIXTURES / domain / "emails.jsonl"
        golden_file = _FIXTURES / domain / "golden.yaml"
        prompt_file = _REPO / "prompts" / f"{domain}.md"

        if not fixture_file.exists():
            pytest.skip(f"No fixture emails for {domain}")
        if not golden_file.exists():
            pytest.skip(f"No golden file for {domain}")

        # Load emails
        emails = []
        for line in fixture_file.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    emails.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

        import yaml
        with open(golden_file) as f:
            golden = yaml.safe_load(f)

        # Call Claude API
        output = self._extract_with_prompt(prompt_file, emails)

        # Score against golden
        score, missed = self._score_extraction(output, golden)

        print(f"\nDomain: {domain}")
        print(f"Score: {score:.0%} ({int(score * len([s for s in golden['extraction_signals'] if s.get('required')]))} / {len([s for s in golden['extraction_signals'] if s.get('required')])} required signals)")
        if missed:
            print(f"Missed: {missed}")

        assert score >= _REQUIRED_SIGNAL_THRESHOLD, (
            f"Prompt extraction accuracy for '{domain}' is {score:.0%} (threshold: {_REQUIRED_SIGNAL_THRESHOLD:.0%})\n"
            f"Missed required signals:\n"
            + "\n".join(f"  - {m}" for m in missed)
            + f"\n\nThis indicates a prompt regression. Check prompts/{domain}.md for changes."
        )
