"""WP-00: precision fixes that make repository-wide PII scanning trustworthy."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".githooks"))
import privacy_scan as ps  # noqa: E402


def _guard():
    sys.path.insert(0, str(REPO / "scripts"))
    import pii_guard  # noqa: PLC0415
    return importlib.reload(pii_guard)


def test_phone_rule_ignores_whitespace_only_run():
    guard = _guard()
    line = "    type: api_call                    # email_send | calendar_write"
    assert guard.scan(line) == (False, {})


def test_phone_rule_still_catches_devanagari_number():
    guard = _guard()
    found, types = guard.scan("फोन: ९८७६५४३२१०")
    assert found and "PHONE" in types


def test_doc_scan_mode_ignores_email_only_rules(monkeypatch):
    guard = _guard()
    text = "होली की शुभकामनाएं"
    assert guard.scan(text)[0] is True            # default: language fence still applies
    monkeypatch.setenv("ARTHA_PII_DOC_SCAN", "1")
    assert guard.scan(text) == (False, {})


def test_doc_scan_mode_still_flags_real_pii(monkeypatch):
    guard = _guard()
    monkeypatch.setenv("ARTHA_PII_DOC_SCAN", "1")
    found, types = guard.scan("ssn 219-" + "09-9999")
    assert found and types


@pytest.mark.parametrize("ssn", ["123-45-6789", "000-00-0000", "987-65-4321", "078-05-1120"])
def test_published_synthetic_ssns_are_not_findings(ssn):
    assert ps.check_secrets(f"example {ssn} here") == []


@pytest.mark.parametrize("card", ["4111 1111 1111 1111", "4111-1111-1111-1111", "4242424242424242",
                                  "5555555555554444", "378282246310005"])
def test_published_test_cards_are_not_findings(card):
    assert ps.check_secrets(f"card {card}") == []


def test_unlisted_ssn_still_a_finding():
    assert ps.check_secrets("ssn 219-" + "09-9999") == ["PII (SSN)"]


def test_age_key_prefix_alone_is_not_a_secret_but_full_key_is():
    prefix_only = "startswith 'AGE-SECRET-KEY-1' literal"
    assert ps.check_secrets(prefix_only) == []
    real_shape = "AGE-SECRET-KEY-1" + "Q" * 58
    assert ps.check_secrets(real_shape) == ["secret (age private key)"]
