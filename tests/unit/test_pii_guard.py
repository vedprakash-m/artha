import pytest
import subprocess
import os
from pathlib import Path

# Paths relative to the project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
PII_GUARD_SH = PROJECT_ROOT / "scripts" / "pii_guard.sh"

def run_pii_guard(cmd, input_text):
    """Helper to run pii_guard.sh and return (stdout, stderr, returncode)."""
    if not PII_GUARD_SH.exists():
        pytest.skip("pii_guard.sh not present (archived) — use Python API tests")
    # Check if bash is available
    import shutil
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found — skipping pii_guard.sh test")

    result = subprocess.run(
        [bash_path, str(PII_GUARD_SH), cmd],
        input=input_text,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT
    )
    return result.stdout, result.stderr, result.returncode

@pytest.mark.parametrize("description, input_text, expected_token", [
    ("SSN with dashes", "Your SSN is 123-45-6789", "[PII-FILTERED-SSN]"),
    ("SSN in statement", "Social Security: 987-65-4321 on file", "[PII-FILTERED-SSN]"),
    ("Visa (spaces)", "Card: 4111 1111 1111 1111 charged", "[PII-FILTERED-CC]"),
    ("Visa (dashes)", "Card: 4111-1111-1111-1111 expired", "[PII-FILTERED-CC]"),
    ("Mastercard", "Card: 5500 0000 0000 0004 approved", "[PII-FILTERED-CC]"),
    ("Amex", "Card: 3714 496353 98431 active", "[PII-FILTERED-CC]"),
    ("Discover", "Card: 6011 1111 1111 1117 active", "[PII-FILTERED-CC]"),
    ("A-number", "Alien Registration: A123456789 filed", "[PII-FILTERED-ANUM]"),
    ("ITIN", "ITIN: 912-78-1234 on record", "[PII-FILTERED-ITIN]"),
    ("Bank routing", "routing number: 021000021 active", "[PII-FILTERED-ROUTING]"),
    ("US Passport", "Passport number: A12345678 verified", "[PII-FILTERED-PASSPORT]"),
    ("WA Driver License", "License: WDLMISH123AB issued", "[PII-FILTERED-DL]"),
])
def test_pii_guard_filter_positive(description, input_text, expected_token):
    """Verify that pii_guard.sh correctly filters various PII types."""
    stdout, stderr, rc = run_pii_guard("filter", input_text)
    assert rc != 0, f"Expected non-zero exit code for {description}"
    assert expected_token in stdout
    # The script logs to stderr with a timestamp and "PII_FILTER"
    assert "PII_FILTER" in stderr
    assert "filtered" in stderr

@pytest.mark.parametrize("description, input_text", [
    ("USCIS IOE receipt", "Receipt: IOE0915220715 received"),
    ("USCIS SRC receipt", "Case: SRC2190050001 pending"),
    ("USCIS LIN receipt", "Receipt LIN2190050001 approved"),
    ("Amazon order", "Order: 112-3456789-1234567 shipped"),
    ("Masked account", "Account ****1234 charged 47.99"),
    ("Clean text", "This is a clean email without any PII."),
])
def test_pii_guard_filter_negative(description, input_text):
    """Verify that pii_guard.sh does NOT filter allowlisted patterns."""
    stdout, stderr, rc = run_pii_guard("filter", input_text)
    assert rc == 0, f"Expected zero exit code for {description}"
    assert stdout == input_text
    # Accept the deprecation warning emitted to stderr by pii_guard.sh, but
    # reject any genuine PII detection output (PII_FOUND / PII_FILTER lines).
    pii_lines = [l for l in stderr.splitlines()
                 if "PII_FOUND" in l or "PII_FILTER" in l]
    assert pii_lines == [], f"Unexpected PII detection in stderr: {pii_lines}"

def test_pii_guard_scan_mode():
    """Verify that scan mode detects PII without modifying output."""
    input_text = "Your SSN is 123-45-6789"
    stdout, stderr, rc = run_pii_guard("scan", input_text)
    assert rc != 0
    assert "PII_FILTER" in stderr
    assert "scan_blocked" in stderr

def test_pii_guard_built_in_test_suite():
    """Run the shell script's own built-in test suite."""
    stdout, stderr, rc = run_pii_guard("test", "")
    assert rc == 0
    assert "Results: 19 passed, 0 failed" in stdout


# ─────────────────────────────────────────────────────────────────────────────
# Python API tests — exercise pii_guard.py scan() and filter_text() directly
# ─────────────────────────────────────────────────────────────────────────────

import sys
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from pii_guard import scan as py_scan, filter_text as py_filter  # noqa: E402


@pytest.mark.parametrize("description, input_text, expected_type", [
    ("SSN with dashes",    "Your SSN is 123-45-6789",            "SSN"),
    ("SSN in statement",   "Social Security: 987-65-4321 on file","SSN"),
    ("Visa card spaces",   "Card: 4111 1111 1111 1111 charged",  "CC"),
    ("Visa card dashes",   "Card: 4111-1111-1111-1111 expired",  "CC"),
    ("Mastercard",         "Card: 5500 0000 0000 0004 approved", "CC"),
    ("Amex",               "Card: 3714 496353 98431 active",     "CC"),
    ("Discover",           "Card: 6011 1111 1111 1117 active",   "CC"),
    ("A-number",           "Alien Registration: A123456789 filed","ANUM"),
    ("ITIN",               "ITIN: 912-78-1234 on record",        "ITIN"),
    ("Bank routing",       "routing number: 021000021 active",   "ROUTING"),
    ("US Passport",        "Passport number: A12345678 verified","PASSPORT"),
    ("WA Driver License",  "License: WDLMISH123AB issued",       "DL"),
])
def test_py_scan_detects_pii(description, input_text, expected_type):
    """pii_guard.scan() returns (True, {type: count}) for PII inputs."""
    found, types = py_scan(input_text)
    assert found, f"scan() should detect PII for: {description}"
    assert expected_type in types, (
        f"Expected type '{expected_type}' in {types} for: {description}"
    )


@pytest.mark.parametrize("description, input_text", [
    ("USCIS IOE receipt",  "Receipt: IOE0915220715 received"),
    ("USCIS SRC receipt",  "Case: SRC2190050001 pending"),
    ("USCIS LIN receipt",  "Receipt LIN2190050001 approved"),
    ("Amazon order",       "Order: 112-3456789-1234567 shipped"),
    ("Masked account",     "Account ****1234 charged 47.99"),
    ("Clean text",         "This is a clean email without any PII."),
])
def test_py_scan_allowlist_passes(description, input_text):
    """pii_guard.scan() returns (False, {}) for allowlisted/clean inputs."""
    found, types = py_scan(input_text)
    assert not found, f"scan() must NOT flag allowlisted pattern: {description}"
    assert types == {}, f"types dict must be empty for: {description}"


@pytest.mark.parametrize("description, input_text, expected_token", [
    ("SSN filter",   "Your SSN is 123-45-6789",           "[PII-FILTERED-SSN]"),
    ("CC filter",    "Card: 4111 1111 1111 1111 charged",  "[PII-FILTERED-CC]"),
    ("ITIN filter",  "ITIN: 912-78-1234 on record",        "[PII-FILTERED-ITIN]"),
    ("ANUM filter",  "Alien Registration: A123456789 filed","[PII-FILTERED-ANUM]"),
])
def test_py_filter_replaces_pii(description, input_text, expected_token):
    """pii_guard.filter_text() replaces PII tokens and returns the filtered string."""
    filtered, types = py_filter(input_text)
    assert expected_token in filtered, (
        f"Expected '{expected_token}' in filtered output for: {description}"
    )
    assert len(types) > 0, f"types dict must be non-empty for: {description}"


def test_py_filter_preserves_allowlist():
    """filter_text() must leave allowlisted patterns intact."""
    text = "Order 112-3456789-1234567 for IOE0915220715 case is clean"
    filtered, types = py_filter(text)
    assert "112-3456789-1234567" in filtered, "Amazon order number must be preserved"
    assert "IOE0915220715" in filtered, "USCIS receipt number must be preserved"
    assert types == {}


def test_py_itin_before_ssn_ordering():
    """ITIN (9XX-[789]X-XXXX) must be caught as ITIN, not SSN."""
    found, types = py_scan("ITIN: 912-78-1234")
    assert found
    assert "ITIN" in types
    assert "SSN" not in types, "ITIN must not be double-counted as SSN"


def test_py_scan_and_filter_agreement():
    """scan() and filter_text() must agree on PII presence."""
    for text in [
        "SSN 123-45-6789",
        "Card 4111 1111 1111 1111",
        "Clean text with no PII here.",
        "Order: 112-3456789-1234567 received",
    ]:
        s_found, s_types = py_scan(text)
        _, f_types = py_filter(text)
        assert s_found == bool(f_types), (
            f"scan/filter disagreement on: {text!r}"
        )


def test_py_builtin_test_suite():
    """pii_guard.py test mode must pass all 19 cases."""
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "pii_guard.py"), "test"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0
    assert "Results: 25 passed, 0 failed" in result.stdout
