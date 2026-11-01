"""tests/unit/test_domain_index_wave1.py — Wave 1 tests for domain_index.py AFW-2

Wave 1 verification suite (specs/agent-fw.md §AFW-2).

Coverage:
  - load_domain_registry(): loads real config/domain_registry.yaml successfully
  - load_domain_registry(): returns {} on bad path (graceful degradation)
  - build_domain_menu(): includes enabled_by_default domains
  - build_domain_menu(): skips disabled domains
  - build_domain_menu(): includes descriptions and up to 5 keywords
  - build_domain_menu(): starts with header line
  - build_domain_menu(): handles empty registry gracefully
  - should_load_domain(): always_load=True → always True
  - should_load_domain(): keyword match in signals → True
  - should_load_domain(): keyword match in user_query → True
  - should_load_domain(): no keyword match → False
  - should_load_domain(): unknown domain → False
  - should_load_domain(): no keywords defined → False
"""
from __future__ import annotations

import pytest

# conftest adds scripts/ to sys.path
from domain_index import build_domain_menu, load_domain_registry, should_load_domain


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal_registry() -> dict:
    """Synthetic registry with two enabled domains and one disabled."""
    return {
        "domains": {
            "finance": {
                "label": "Finance",
                "description": "Bills and banking",
                "always_load": True,
                "enabled_by_default": True,
                "routing_keywords": ["bill", "payment", "bank", "tax", "invoice"],
            },
            "immigration": {
                "label": "Immigration",
                "description": "USCIS filings and visa status",
                "always_load": True,
                "enabled_by_default": True,
                "routing_keywords": ["USCIS", "I-485", "visa", "EAD", "green card"],
            },
            "learning": {
                "label": "Learning",
                "description": "Courses and certifications",
                "always_load": False,
                "enabled_by_default": True,
                "routing_keywords": ["course", "certification", "study", "exam", "training"],
            },
            "pets": {
                "label": "Pets",
                "description": "Pet care and vet visits",
                "always_load": False,
                "enabled_by_default": False,   # DISABLED
                "routing_keywords": ["vet", "grooming", "flea"],
            },
        }
    }


# ---------------------------------------------------------------------------
# load_domain_registry()
# ---------------------------------------------------------------------------

class TestLoadDomainRegistry:
    def test_loads_real_registry(self):
        """load_domain_registry() loads the real config/domain_registry.yaml."""
        registry = load_domain_registry()
        assert isinstance(registry, dict)
        assert "domains" in registry
        assert len(registry["domains"]) > 0

    def test_returns_dict_on_bad_path(self, tmp_path):
        """Bad path returns empty dict without raising."""
        result = load_domain_registry(artha_dir=tmp_path / "no_such_dir")
        assert result == {}

    def test_real_registry_has_finance(self):
        """Real registry includes finance domain."""
        registry = load_domain_registry()
        assert "finance" in registry.get("domains", {})

    def test_real_registry_has_immigration(self):
        """Real registry includes immigration domain."""
        registry = load_domain_registry()
        assert "immigration" in registry.get("domains", {})

    def test_real_domain_has_routing_keywords(self):
        """Most domains in real registry have routing_keywords list."""
        registry = load_domain_registry()
        domains = registry.get("domains", {})
        domains_with_kw = [n for n, cfg in domains.items() if "routing_keywords" in cfg]
        # At least 60% of domains should have routing_keywords; core domains always do
        assert len(domains_with_kw) >= len(domains) * 0.6, (
            f"Only {len(domains_with_kw)}/{len(domains)} domains have routing_keywords"
        )
        # Core always-load domains must have routing_keywords
        for name in ("finance", "immigration", "health"):
            assert "routing_keywords" in domains.get(name, {}), f"{name} missing routing_keywords"


# ---------------------------------------------------------------------------
# build_domain_menu()
# ---------------------------------------------------------------------------

class TestBuildDomainMenu:
    def test_returns_string(self):
        """build_domain_menu() returns a string."""
        registry = _minimal_registry()
        result = build_domain_menu(registry)
        assert isinstance(result, str)

    def test_starts_with_header(self):
        """First line is the expected header."""
        result = build_domain_menu(_minimal_registry())
        assert result.startswith("Available domains")

    def test_includes_enabled_domains(self):
        """Enabled-by-default domains appear in the menu."""
        result = build_domain_menu(_minimal_registry())
        assert "finance" in result
        assert "immigration" in result
        assert "learning" in result

    def test_excludes_disabled_domains(self):
        """Domains with enabled_by_default=False are excluded."""
        result = build_domain_menu(_minimal_registry())
        assert "pets" not in result

    def test_includes_description(self):
        """Domain descriptions appear in the menu."""
        result = build_domain_menu(_minimal_registry())
        assert "Bills and banking" in result
        assert "USCIS filings" in result

    def test_includes_keywords(self):
        """Routing keywords appear in the menu."""
        result = build_domain_menu(_minimal_registry())
        assert "bill" in result
        assert "USCIS" in result

    def test_at_most_five_keywords(self):
        """Menu shows at most 5 keywords per domain even if registry has more."""
        registry = {
            "domains": {
                "test_domain": {
                    "description": "Lots of keywords",
                    "enabled_by_default": True,
                    "routing_keywords": ["k1", "k2", "k3", "k4", "k5", "k6", "k7"],
                }
            }
        }
        result = build_domain_menu(registry)
        # Should have k1-k5 but not k6 or k7
        assert "k5" in result
        assert "k6" not in result
        assert "k7" not in result

    def test_empty_registry_returns_header(self):
        """Empty registry returns only the header line."""
        result = build_domain_menu({})
        assert result.startswith("Available domains")
        # No domain lines
        lines = result.strip().split("\n")
        assert len(lines) == 1

    def test_empty_domains_dict(self):
        """domains: {} returns only the header line."""
        result = build_domain_menu({"domains": {}})
        lines = result.strip().split("\n")
        assert len(lines) == 1

    def test_domain_without_keywords(self):
        """Domain with no routing_keywords still appears, without keyword tag."""
        registry = {
            "domains": {
                "nokw": {
                    "description": "No keywords here",
                    "enabled_by_default": True,
                    "routing_keywords": [],
                }
            }
        }
        result = build_domain_menu(registry)
        assert "nokw" in result
        assert "keywords:" not in result

    def test_real_registry_menu_nonempty(self):
        """build_domain_menu() on real registry returns a non-trivial menu."""
        registry = load_domain_registry()
        menu = build_domain_menu(registry)
        lines = menu.split("\n")
        # Expect at least 10 domain lines (Artha has 39+ domains)
        assert len(lines) >= 10


# ---------------------------------------------------------------------------
# should_load_domain()
# ---------------------------------------------------------------------------

class TestShouldLoadDomain:
    def test_always_load_true(self):
        """always_load=True → True regardless of signals/query."""
        registry = _minimal_registry()
        assert should_load_domain("finance", [], "", registry) is True
        assert should_load_domain("immigration", [], "", registry) is True

    def test_keyword_in_signals(self):
        """Keyword appearing in signals → True."""
        registry = _minimal_registry()
        assert should_load_domain("learning", ["course completion email"], "brief", registry) is True

    def test_keyword_in_user_query(self):
        """Keyword appearing in user_query → True."""
        registry = _minimal_registry()
        assert should_load_domain("learning", [], "what's my study status", registry) is True

    def test_no_match_returns_false(self):
        """No keyword match → False."""
        registry = _minimal_registry()
        assert should_load_domain("learning", ["email from boss"], "brief me", registry) is False

    def test_unknown_domain_returns_false(self):
        """Unknown domain name → False."""
        registry = _minimal_registry()
        assert should_load_domain("nonexistent", ["anything"], "anything", registry) is False

    def test_missing_domain_key_returns_false(self):
        """Domain exists but has no routing_keywords and no always_load → False."""
        registry = {
            "domains": {
                "empty": {
                    "description": "nothing",
                    "enabled_by_default": True,
                }
            }
        }
        assert should_load_domain("empty", ["email"], "brief", registry) is False

    def test_empty_keywords_returns_false(self):
        """Domain with empty routing_keywords list → False."""
        registry = {
            "domains": {
                "sparse": {
                    "description": "no kw",
                    "enabled_by_default": True,
                    "always_load": False,
                    "routing_keywords": [],
                }
            }
        }
        assert should_load_domain("sparse", ["lots of signals"], "anything", registry) is False

    def test_case_insensitive_matching(self):
        """Keyword matching is case-insensitive."""
        registry = _minimal_registry()
        # "USCIS" is a keyword for immigration — test with lowercase signal
        # immigration has always_load=True so we can't test keyword match
        # Use learning domain: keywords: ["course", "certification", ...]
        assert should_load_domain("learning", ["COURSE AVAILABLE"], "brief", registry) is True

    def test_partial_match(self):
        """Keywords match as substrings of signal text."""
        registry = _minimal_registry()
        # "certification" in "new certification available now"
        assert should_load_domain(
            "learning",
            ["new certification available now"],
            "",
            registry,
        ) is True

    def test_empty_registry_returns_false(self):
        """Empty registry → False for any domain."""
        assert should_load_domain("finance", ["bill"], "pay bill", {}) is False
