"""tests/integration/test_signal_pipeline.py — DEBT-EVAL-001
=============================================================
Integration test: connector record → signal extraction pipeline.

Goals:
1. Precision: known-trigger emails produce the expected signal_type
2. Recall: known-noise emails produce no high-confidence signal
3. No-LLM: entire pipeline runs without any LLM call (companion to DEBT-EVAL-003)
4. PII hygiene: signal metadata values do not contain raw OTP/PAN patterns

These tests use no mocks beyond blocking LLM modules in sys.modules.
They run against real pipeline code with fixture data only.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"
_FIXTURES_DIR = _REPO / "tests" / "fixtures"
_SIGNALS_FIXTURE = _FIXTURES_DIR / "email_signals.yaml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixtures() -> list[dict]:
    if not _SIGNALS_FIXTURE.exists():
        return []
    return yaml.safe_load(_SIGNALS_FIXTURE.read_text(encoding="utf-8")) or []


def _make_blocked_module(name: str) -> types.ModuleType:
    """Return a module stub that raises ImportError on any attribute access."""
    class _Blocked(types.ModuleType):
        def __getattr__(self, attr: str):
            raise ImportError(
                f"LLM client '{self.__name__}.{attr}' accessed during signal pipeline — "
                "violates no-LLM-in-signal-path invariant (DEBT-EVAL-001)"
            )
    return _Blocked(name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def extractor():
    """EmailSignalExtractor instance, loaded with LLM modules blocked."""
    blocked = {
        "anthropic": _make_blocked_module("anthropic"),
        "openai": _make_blocked_module("openai"),
        "litellm": _make_blocked_module("litellm"),
    }
    # Clear any cached imports from other test modules
    for mod_name in list(sys.modules.keys()):
        if "email_signal_extractor" in mod_name:
            del sys.modules[mod_name]

    with patch.dict("sys.modules", blocked):
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        from email_signal_extractor import EmailSignalExtractor  # type: ignore[import]
        return EmailSignalExtractor()


class TestSignalPipelinePrecision:
    """Known-trigger emails must produce the expected signal_type (precision)."""

    @pytest.mark.parametrize("fixture", [f for f in _load_fixtures() if f.get("expect_match")])
    def test_email_triggers_expected_signal(self, extractor, fixture):
        email = fixture["email"]
        expected_type = fixture["expect_signal"]
        if not expected_type:
            return  # skip null-signal cases here

        signals = extractor.extract([email])
        # After v3.35.0 consolidation: signal_type holds canonical type;
        # subtype holds the original specific type. Check subtype first.
        subtypes = [getattr(s, "subtype", s.signal_type) for s in signals]
        assert expected_type in subtypes, (
            f"fixture '{fixture['id']}': expected subtype '{expected_type}' "
            f"but got {subtypes!r}"
        )


class TestSignalPipelineRecall:
    """Known-noise emails must NOT produce high-confidence signals (recall)."""

    @pytest.mark.parametrize("fixture", [f for f in _load_fixtures() if not f.get("expect_match")])
    def test_noise_email_no_high_confidence_signal(self, extractor, fixture):
        email = fixture["email"]
        expected_type = fixture["expect_signal"]

        signals = extractor.extract([email])
        # If a specific signal_type is declared as expect_signal=null, any extraction is allowed —
        # but if expect_match=false, we assert no HIGH-confidence signal for that specific type
        if expected_type is not None:
            high_conf = [
                s for s in signals
                if getattr(s, "signal_type", None) == expected_type
                and getattr(s, "confidence", 0) >= 0.8
            ]
            assert not high_conf, (
                f"fixture '{fixture['id']}': noise email produced high-confidence signal "
                f"'{expected_type}' — {high_conf!r}"
            )


class TestSignalPipelinePIIHygiene:
    """Signal metadata values must not contain raw OTP or banking-style numeric patterns."""

    _OTP_RE = re.compile(r"\b\d{4,8}\b")   # 4-8 digit codes

    def test_otp_email_signal_metadata_clean(self, extractor):
        """OTP email either produces no signal, or metadata values contain no raw OTP codes."""
        otp_email = {
            "id": "otp_test_99",
            "subject": "OTP: 482391 — do not share",
            "from": "security@bank.com",
            "snippet": "Your one-time password is 482391. Valid for 5 minutes.",
        }
        signals = extractor.extract([otp_email])
        for sig in signals:
            metadata = getattr(sig, "metadata", {}) or {}
            for key, val in metadata.items():
                if isinstance(val, str):
                    # Allowed structural keys
                    if key in {"email_id", "sensitivity", "signal_origin", "source_id"}:
                        continue
                    hits = self._OTP_RE.findall(val)
                    assert not hits, (
                        f"Signal metadata['{key}'] contains potential OTP digits {hits!r} — "
                        "PII scrub failed (DEBT-SIG-004 / DEBT-EVAL-001)"
                    )


class TestSignalPipelineNoLLM:
    """The entire extraction must complete with LLM modules blocked at import time."""

    def test_pipeline_runs_without_llm_clients(self):
        """Confirm extractor works end-to-end with anthropic/openai fully removed."""
        blocked = {
            "anthropic": _make_blocked_module("anthropic"),
            "openai": _make_blocked_module("openai"),
        }
        # Evict cached imports
        for mod_name in list(sys.modules.keys()):
            if "email_signal_extractor" in mod_name:
                del sys.modules[mod_name]

        with patch.dict("sys.modules", blocked):
            if str(_SCRIPTS) not in sys.path:
                sys.path.insert(0, str(_SCRIPTS))
            from email_signal_extractor import EmailSignalExtractor  # type: ignore[import]
            ext = EmailSignalExtractor()
            result = ext.extract([{
                "id": "llm_block_e2e",
                "subject": "Rent due December 31",
                "from": "landlord@test.com",
                "snippet": "Pay your rent of $2,400 by 2026-12-31.",
            }])
            assert isinstance(result, list), "extract() must return a list when LLM modules absent"
