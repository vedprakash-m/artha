"""tests/unit/test_correction_feeder.py — Unit tests for scripts/correction_feeder.py.

All facts are synthetic — no real user data (DD-5).
Ref: specs/eval.md EV-9, T-EV-9-01 through T-EV-9-11
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def feeder():
    return _load_module("correction_feeder", _SCRIPTS_DIR / "correction_feeder.py")


# ---------------------------------------------------------------------------
# Fact helpers
# ---------------------------------------------------------------------------

def _future_ttl(days: int = 365) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _past_ttl(days: int = 1) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _make_fact(
    type_: str = "correction",
    domain: str = "finance",
    value: str = "test value",
    ttl: str | None = None,
) -> dict:
    return {
        "type": type_,
        "domain": domain,
        "value": value,
        "ttl": ttl or _future_ttl(),
    }


# ===========================================================================
# T-EV-9-01: type not in _ALLOWED_TYPES is filtered
# ===========================================================================

def test_disallowed_type_is_filtered(feeder):
    """T-EV-9-01: Facts with type 'note' (not allowed) must be removed."""
    facts = [_make_fact(type_="note")]
    result = feeder._filter_facts(facts)
    assert len(result) == 0, f"Expected empty result but got: {result}"


# ===========================================================================
# T-EV-9-02: expired TTL is filtered
# ===========================================================================

def test_expired_ttl_is_filtered(feeder):
    """T-EV-9-02: Facts with past TTL must be removed."""
    facts = [_make_fact(ttl=_past_ttl())]
    result = feeder._filter_facts(facts)
    assert len(result) == 0, f"Expected empty result but got: {result}"


# ===========================================================================
# T-EV-9-03: valid correction passes through
# ===========================================================================

def test_valid_correction_passes_filter(feeder):
    """T-EV-9-03: Valid non-expired correction must pass through the filter."""
    facts = [_make_fact(value="Use gross income for mortgage calc")]
    result = feeder._filter_facts(facts)
    assert len(result) == 1
    assert result[0]["value"] == "Use gross income for mortgage calc"


# ===========================================================================
# T-EV-9-04: domain filter excludes foreign domain
# ===========================================================================

def test_domain_filter_excludes_foreign_domain(feeder):
    """T-EV-9-04: With domain='finance' filter, immigration facts must be excluded."""
    facts = [
        _make_fact(domain="finance", value="Finance fact"),
        _make_fact(domain="immigration", value="Immigration fact"),
    ]
    result = feeder._filter_facts(facts, domain="finance")
    domains = {f["domain"] for f in result}
    assert "immigration" not in domains, f"Immigration leaked through domain filter: {result}"
    assert "finance" in domains


# ===========================================================================
# T-EV-9-05: per-domain cap at 10 (11th excluded)
# ===========================================================================

def test_per_domain_cap_enforced(feeder):
    """T-EV-9-05: 11th fact in same domain must be excluded (per-domain cap = 10)."""
    facts = [_make_fact(domain="finance", value=f"fact-{i}") for i in range(11)]
    result = feeder._filter_facts(facts)
    finance_facts = [f for f in result if f["domain"] == "finance"]
    assert len(finance_facts) == 10, (
        f"Expected 10 finance facts (cap), got {len(finance_facts)}"
    )


# ===========================================================================
# T-EV-9-06: global cap 50 enforced
# ===========================================================================

def test_global_cap_enforced(feeder):
    """T-EV-9-06: Global cap of 50 must be enforced across all domains."""
    # 5 domains × 10 facts = 50; adding 5 more across domains
    domains = ["finance", "immigration", "health", "kids", "home",
               "travel", "learning", "vehicle", "insurance", "estate"]
    facts = [
        _make_fact(domain=domains[i % len(domains)], value=f"fact-{i}")
        for i in range(55)
    ]
    result = feeder._filter_facts(facts)
    assert len(result) <= 50, f"Expected at most 50 facts, got {len(result)}"


# ===========================================================================
# T-EV-9-07: _strip_pii replaces email
# ===========================================================================

def test_strip_pii_replaces_email(feeder):
    """T-EV-9-07: _strip_pii must replace email addresses with [EMAIL]."""
    cleaned = feeder._strip_pii("Contact john.doe@example.com for details.")
    assert "[EMAIL]" in cleaned, f"Email not replaced: '{cleaned}'"
    assert "john.doe@example.com" not in cleaned


# ===========================================================================
# T-EV-9-08: _strip_pii replaces name pattern
# ===========================================================================

def test_strip_pii_replaces_name(feeder):
    """T-EV-9-08: _strip_pii must replace title-case name patterns with [NAME]."""
    # Two consecutive title-case words — matches the name pattern
    cleaned = feeder._strip_pii("Confirmed by John Smith yesterday.")
    assert "[NAME]" in cleaned, f"Name not replaced: '{cleaned}'"
    assert "John Smith" not in cleaned


# ===========================================================================
# T-EV-9-09: _is_expired: future TTL → not expired
# ===========================================================================

def test_is_expired_future_ttl(feeder):
    """T-EV-9-09: Fact with future TTL must not be expired."""
    fact = {"ttl": _future_ttl(365)}
    assert feeder._is_expired(fact) is False


# ===========================================================================
# T-EV-9-10: _is_expired: past TTL → expired
# ===========================================================================

def test_is_expired_past_ttl(feeder):
    """T-EV-9-10: Fact with past TTL must be expired."""
    fact = {"ttl": _past_ttl(1)}
    assert feeder._is_expired(fact) is True


# ===========================================================================
# T-EV-9-11: _is_enabled() returns False when config disabled
# ===========================================================================

def test_is_enabled_false_when_config_disabled(feeder, tmp_path, monkeypatch):
    """T-EV-9-11: _is_enabled() must return False when config flag is false."""
    import yaml  # type: ignore[import]

    cfg_file = tmp_path / "artha_config.yaml"
    cfg_file.write_text(
        yaml.dump({
            "harness": {"eval": {"correction_injection": {"enabled": False}}}
        })
    )
    monkeypatch.setattr(feeder, "_CONFIG_FILE", cfg_file)
    assert feeder._is_enabled() is False
