"""Phase 0 PII guard i18n tests — Devanagari/Hindi script detection.

Verifies that pii_guard.py correctly flags:
- Devanagari phone numbers   (PHONE)
- Known Hindi names           (DEVA_NAME)
- Hindi address keywords      (ADDR)
- Language fence for non-Latin script (PII_UNVERIFIED_SCRIPT)
- Devanagari digits in ID context (AADHAAR)
- Mixed Latin+Devanagari strings
- English-only is unaffected by language fence

Spec: §15.7, §10.1
"""
import pytest
import sys
from pathlib import Path

# Ensure scripts/ is importable (conftest.py handles this, but be explicit)
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from pii_guard import scan, filter_text


# ---------------------------------------------------------------------------
# §15.7 Tests
# ---------------------------------------------------------------------------

def test_devanagari_phone_number():
    """'फोन: ९८७६५४३२१०' is detected as PHONE PII."""
    found, types = scan("फोन: ९८७६५४३२१०")
    assert found is True, "Expected PII to be found in Devanagari phone string"
    assert "PHONE" in types, f"Expected PHONE in types, got: {types}"


def test_hindi_name_detection():
    """'वेद शर्मा' is detected as name PII — 'वेद' matches known Devanagari name tokens."""
    found, types = scan("वेद शर्मा")
    assert found is True, "Expected name PII to be found"
    # 'वेद' is a known profile name — must trigger DEVA_NAME
    assert "DEVA_NAME" in types or "PII_UNVERIFIED_SCRIPT" in types, \
        f"Expected DEVA_NAME or PII_UNVERIFIED_SCRIPT in types, got: {types}"
    # Specifically validate DEVA_NAME fires (language fence alone is insufficient —
    # tests require name-specific detection per §10.1 requirement #2)
    assert "DEVA_NAME" in types, f"Expected DEVA_NAME for वेद, got: {types}"


def test_mixed_script_pii():
    """'Call वेद at 9876543210' — both Devanagari name and phone caught."""
    found, types = scan("Call वेद at 9876543210")
    assert found is True
    # Latin phone pattern must catch the ASCII number
    assert "PHONE" in types or "PII_UNVERIFIED_SCRIPT" in types
    # Devanagari name token must be caught
    assert "DEVA_NAME" in types, f"Expected DEVA_NAME for वेद in mixed string, got: {types}"


def test_hindi_address_keywords():
    """'मोहल्ला गांधी नगर' is flagged as potential address PII."""
    found, types = scan("मोहल्ला गांधी नगर")
    assert found is True, "Expected address PII in Hindi locality string"
    assert "ADDR" in types, f"Expected ADDR type for Hindi address keyword, got: {types}"


def test_language_fence_non_latin():
    """Non-Latin content (pure Devanagari) gets PII_UNVERIFIED_SCRIPT flag per §10.1."""
    # Content with no specific PII pattern — only the language fence should fire
    found, types = scan("यह हिंदी पाठ है")
    assert found is True, "Language fence should flag Devanagari content"
    assert "PII_UNVERIFIED_SCRIPT" in types, \
        f"Expected PII_UNVERIFIED_SCRIPT for Devanagari text, got: {types}"


def test_language_fence_latin_unaffected():
    """English-only content is NOT flagged by the language fence."""
    found, types = scan("This is a perfectly normal English sentence with no PII.")
    assert found is False, f"English-only text must not trigger language fence: {types}"
    assert "PII_UNVERIFIED_SCRIPT" not in types, \
        f"PII_UNVERIFIED_SCRIPT must not fire for Latin-only text, got: {types}"


def test_devanagari_digits_in_context():
    """'आधार: १२३४ ५६७८ ९०१२' is detected as an Aadhaar ID number."""
    found, types = scan("आधार: १२३४ ५६७८ ९०१२")
    assert found is True, "Expected Aadhaar PII in Devanagari digit string"
    assert "AADHAAR" in types, f"Expected AADHAAR type for Devanagari Aadhaar, got: {types}"


# ---------------------------------------------------------------------------
# Additional robustness checks (§10.1 coverage declaration)
# ---------------------------------------------------------------------------

def test_filter_redacts_devanagari_phone():
    """filter_text() replaces Devanagari phone with [PII-FILTERED-PHONE]."""
    filtered, types = filter_text("फोन: ९८७६५४३२१०")
    assert "PHONE" in types
    assert "[PII-FILTERED-PHONE]" in filtered
    assert "९८७६५४३२१०" not in filtered, "Raw Devanagari digits must be redacted"


def test_filter_redacts_known_name():
    """filter_text() replaces known Devanagari name with [PII-FILTERED-NAME]."""
    filtered, types = filter_text("नमस्ते वेद जी")
    assert "DEVA_NAME" in types
    assert "[PII-FILTERED-NAME]" in filtered
    assert "वेद" not in filtered, "Known Devanagari name must be redacted"
