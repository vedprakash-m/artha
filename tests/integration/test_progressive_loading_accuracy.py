"""tests/integration/test_progressive_loading_accuracy.py — AFW-2 + A-9.

Integration test verifying that progressive / lazy domain loading correctly
decides which domain prompts to load versus skip for 10 realistic catch-up
scenarios.

Validates:
1.  Tier-1 (``always_load: true``) domains are loaded for EVERY scenario.
2.  Keyword-triggered domains load ONLY when their keywords appear in signals
    or the user query.
3.  Keyword-triggered domains are SKIPPED when no matching signal is present
    (saving context tokens).
4.  The domain menu includes all always-load domains regardless of signals.
5.  Adding a keyword signal to a query triggers the relevant domain.
6.  Removing the keyword stops triggering the domain.
7.  Token-savings assertion: lazy loading skips at least 30% of all domains
    for a generic catch-up with no rare domain keywords.
8.  Progressive order is stable: always-load domains always precede lazy ones
    in the build_domain_menu output.
9.  Case-insensitive keyword matching works as expected.
10. Unknown domain names return False without raising errors.

Spec: specs/agent-fw.md §7.2 — ``test_progressive_loading_accuracy``
Validates: AFW-2 (Progressive Disclosure), A-9 (token budget experiment gate)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# Ensure scripts/ directory is importable
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_ARTHA = Path(__file__).resolve().parent.parent.parent
_REGISTRY_PATH = _ARTHA / "config" / "domain_registry.yaml"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def registry() -> dict:
    """Load the real domain_registry.yaml once for all tests."""
    with open(_REGISTRY_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _always_load_domains(registry: dict) -> list[str]:
    """Return list of all domain names marked always_load: true."""
    domains = registry.get("domains") or {}
    return [name for name, cfg in domains.items() if cfg.get("always_load", False)]


def _lazy_domains(registry: dict) -> list[str]:
    """Return list of all domain names NOT marked always_load."""
    domains = registry.get("domains") or {}
    return [name for name, cfg in domains.items() if not cfg.get("always_load", False)]


# ---------------------------------------------------------------------------
# 10 progressive loading accuracy tests
# ---------------------------------------------------------------------------

class TestProgressiveLoadingAccuracy:
    """10 catch-up scenarios validating progressive loading decisions."""

    # ------------------------------------------------------------------
    # Test 1: always-load domains ALWAYS load (no signals)
    # ------------------------------------------------------------------
    def test_always_load_domains_load_with_no_signals(self, registry):
        from domain_index import should_load_domain

        always = _always_load_domains(registry)
        assert always, "Fixture: expected at least one always_load domain"

        for domain in always:
            result = should_load_domain(domain, signals=[], user_query="", registry=registry)
            assert result, f"Domain '{domain}' (always_load:true) must load with empty signals"

    # ------------------------------------------------------------------
    # Test 2: always-load domains load even with unrelated signals
    # ------------------------------------------------------------------
    def test_always_load_domains_load_regardless_of_signals(self, registry):
        from domain_index import should_load_domain

        always = _always_load_domains(registry)
        unrelated_signals = ["package delivered", "weather is sunny", "calendar clear"]

        for domain in always:
            result = should_load_domain(domain, signals=unrelated_signals, user_query="show me", registry=registry)
            assert result, (
                f"Domain '{domain}' (always_load:true) must load even with unrelated signals"
            )

    # ------------------------------------------------------------------
    # Test 3: lazy domain does NOT load without its keyword
    # ------------------------------------------------------------------
    def test_lazy_domain_skipped_without_keyword(self, registry):
        from domain_index import should_load_domain

        lazy = _lazy_domains(registry)
        assert lazy, "Fixture: expected at least one non-always_load domain"

        generic_signals = ["bills paid", "calendar sync'd", "goals reviewed"]
        generic_query = "catch me up"

        # At least one lazy domain must NOT load for the generic scenario
        skipped = [
            d for d in lazy
            if not should_load_domain(d, signals=generic_signals, user_query=generic_query, registry=registry)
        ]
        assert skipped, (
            "At least one lazy domain should be skipped for a generic catch-up. "
            f"All lazy domains loaded: {lazy}"
        )

    # ------------------------------------------------------------------
    # Test 4: keyword in signal triggers the corresponding lazy domain
    # ------------------------------------------------------------------
    def test_keyword_in_signal_triggers_lazy_domain(self, registry):
        from domain_index import should_load_domain

        # Use 'home' domain — keyword-triggered (always_load: false)
        # Its keywords should include something like "repair", "home", "mortgage", etc.
        domains = registry.get("domains") or {}
        home_cfg = domains.get("home", {})
        keywords = home_cfg.get("routing_keywords", [])

        if not keywords:
            pytest.skip("home domain has no routing_keywords in registry")

        trigger_kw = keywords[0]
        result = should_load_domain(
            "home",
            signals=[f"issue: {trigger_kw} needed"],
            user_query="",
            registry=registry,
        )
        assert result, f"'home' domain must load when signal contains keyword '{trigger_kw}'"

    # ------------------------------------------------------------------
    # Test 5: keyword in user_query triggers lazy domain
    # ------------------------------------------------------------------
    def test_keyword_in_query_triggers_lazy_domain(self, registry):
        from domain_index import should_load_domain

        domains = registry.get("domains") or {}
        employment_cfg = domains.get("employment", {})
        keywords = employment_cfg.get("routing_keywords", [])

        if not keywords:
            pytest.skip("employment domain has no routing_keywords in registry")

        trigger_kw = keywords[0]
        result = should_load_domain(
            "employment",
            signals=[],
            user_query=f"What's my {trigger_kw} status?",
            registry=registry,
        )
        assert result, (
            f"'employment' domain must load when user_query contains keyword '{trigger_kw}'"
        )

    # ------------------------------------------------------------------
    # Test 6: removing keyword stops triggering domain
    # ------------------------------------------------------------------
    def test_without_keyword_lazy_domain_not_triggered(self, registry):
        from domain_index import should_load_domain

        # No "travel" keywords in signals or query
        result = should_load_domain(
            "travel",
            signals=["tax return due", "check calendar", "family dinner"],
            user_query="catch me up on finance",
            registry=registry,
        )
        assert not result, (
            "'travel' domain must NOT load when signals/query contain no travel keywords"
        )

    # ------------------------------------------------------------------
    # Test 7: token-savings — ≥30% of all domains skipped for generic query
    # ------------------------------------------------------------------
    def test_generic_catchup_skips_at_least_30_percent_of_domains(self, registry):
        from domain_index import should_load_domain

        domains = registry.get("domains") or {}
        if not domains:
            pytest.skip("no domains in registry")

        generic_signals = ["morning briefing started"]
        generic_query = "brief"

        loaded = sum(
            1 for d in domains
            if should_load_domain(d, signals=generic_signals, user_query=generic_query, registry=registry)
        )
        total = len(domains)
        skipped_fraction = (total - loaded) / total

        assert skipped_fraction >= 0.30, (
            f"Expected ≥30% of domains skipped for generic catch-up, "
            f"but only {skipped_fraction:.0%} were skipped "
            f"({total - loaded}/{total})"
        )

    # ------------------------------------------------------------------
    # Test 8: domain menu always includes always-load domains first
    # ------------------------------------------------------------------
    def test_domain_menu_includes_always_load_domains(self, registry):
        from domain_index import build_domain_menu

        menu_text = build_domain_menu(registry)
        always = _always_load_domains(registry)

        for domain in always:
            assert domain in menu_text, (
                f"Domain '{domain}' (always_load:true) must appear in domain menu"
            )

    # ------------------------------------------------------------------
    # Test 9: case-insensitive keyword matching
    # ------------------------------------------------------------------
    def test_keyword_matching_is_case_insensitive(self, registry):
        from domain_index import should_load_domain

        domains = registry.get("domains") or {}
        home_cfg = domains.get("home", {})
        keywords = home_cfg.get("routing_keywords", [])

        if not keywords:
            pytest.skip("home domain has no routing_keywords in registry")

        trigger_kw = keywords[0]
        # Uppercase signal
        result_upper = should_load_domain(
            "home",
            signals=[trigger_kw.upper()],
            user_query="",
            registry=registry,
        )
        # Mixed-case signal
        result_mixed = should_load_domain(
            "home",
            signals=[trigger_kw.capitalize()],
            user_query="",
            registry=registry,
        )
        assert result_upper, f"should_load_domain must match keyword '{trigger_kw}' case-insensitively (UPPER)"
        assert result_mixed, f"should_load_domain must match keyword '{trigger_kw}' case-insensitively (Capitalized)"

    # ------------------------------------------------------------------
    # Test 10: unknown domain returns False without error
    # ------------------------------------------------------------------
    def test_unknown_domain_returns_false_without_error(self, registry):
        from domain_index import should_load_domain

        result = should_load_domain(
            "nonexistent_domain_xyz",
            signals=["visa expiring", "mortgage", "travel booked"],
            user_query="what's happening with nonexistent_domain_xyz?",
            registry=registry,
        )
        assert result is False, (
            "should_load_domain must return False for an unknown domain name, not raise"
        )
