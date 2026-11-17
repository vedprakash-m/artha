"""Unit tests for scripts/pii_guard.py Python API and built-in CLI self-test."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
from pii_guard import filter_text as py_filter, scan as py_scan  # noqa: E402


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
    """pii_guard.py test mode must pass all 25 cases."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "pii_guard.py"), "test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, f"pii_guard.py test failed:\n{result.stdout}"
    assert "0 failed" in result.stdout


def test_name_in_subject_pattern_hooks_defined():
    import pii_guard  # noqa: PLC0415

    assert callable(getattr(pii_guard, "_maybe_add_name_in_subject_pattern", None))
    assert hasattr(pii_guard, "_NAME_IN_SUBJECT_LOADED")


def test_core_pii_categories_action_payload_and_audit_log(tmp_path, monkeypatch):
    import pii_guard  # noqa: PLC0415

    # In-process builtin regression suite covers regex categories cleanly
    with pytest.raises(SystemExit) as exc:
        pii_guard.do_test()
    assert exc.value.code == 0

    # Verify scan_action_payload across text fields & non-string ignore + telemetry failure resilience
    payload = {
        "recipient": "bob@example.com",
        "payee": "IRS",
        "body": "SSN: 123-45-6789 and card 4111-1111-1111-1111",
        "notes": "Routing # 021000021",
        "amount": 500,  # non-string ignored
        "extra_ignored_field": "SSN 999-99-9999",
    }
    labels = pii_guard.scan_action_payload(payload)
    assert "SSN" in labels and "CC" in labels
    assert pii_guard.scan_action_payload({"subject": "Hello", "body": ""}) == []

    # Verify audit logging + pii-guard: ignore-file header directive (first 5 lines)
    audit_file = tmp_path / "audit.md"
    monkeypatch.setattr(pii_guard, "_AUDIT_LOG", audit_file)
    pii_guard._log_to_audit("SSN,CC", "REDACTED")
    assert "PII_FILTER | email_id:" in audit_file.read_text(encoding="utf-8")

    ignored = tmp_path / "ignored.py"
    ignored.write_text("# header\n# pii-guard: ignore-file\nx = 1\n", encoding="utf-8")
    not_ignored = tmp_path / "normal.py"
    not_ignored.write_text("\n" * 6 + "# pii-guard: ignore-file\n", encoding="utf-8")
    assert pii_guard._is_ignored_file(ignored) is True
    assert pii_guard._is_ignored_file(not_ignored) is False
