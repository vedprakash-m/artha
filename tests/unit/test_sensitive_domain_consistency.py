"""
tests/unit/test_sensitive_domain_consistency.py
=================================================
DEBT-002 / DEBT-034 / DEBT-006 / DEBT-007:
Verifies that all sensitive-domain consumers derive from a single source of
truth (foundation.get_sensitive_domains()) and that the expected 16 domains
are protected.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

# ── Ensure scripts/ is importable ────────────────────────────────────────────
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _foundation_domains() -> frozenset[str]:
    from foundation import get_sensitive_domains
    return get_sensitive_domains()


class TestSensitiveDomainConsistency:
    """A3 (DEBT-002): All consumers produce identical sensitive domain sets."""

    def test_foundation_has_12_domains(self):
        """A1: foundation.py lists exactly 16 sensitive domains (DEBT-034 + DEBT-006 + RD-44)."""
        domains = _foundation_domains()
        assert len(domains) == 16, f"Expected 16 domains, got {len(domains)}: {sorted(domains)}"

    def test_kids_in_foundation(self):
        """DEBT-034: kids domain is vault-protected."""
        assert "kids" in _foundation_domains()

    def test_employment_in_foundation(self):
        """DEBT-006: employment domain is vault-protected."""
        assert "employment" in _foundation_domains()

    def test_all_original_10_present(self):
        """Original 10 domains must still be present after additions."""
        original = {
            "immigration", "finance", "insurance", "estate", "health",
            "audit", "vehicle", "contacts", "occasions", "transactions",
        }
        domains = _foundation_domains()
        missing = original - domains
        assert not missing, f"Original domains missing: {missing}"

    def test_vault_hook_matches_foundation(self):
        """A2: vault_hook.py SENSITIVE_DOMAINS matches get_sensitive_domains()."""
        expected = _foundation_domains()
        # Load vault_hook directly (it may not be importable as a module)
        spec = importlib.util.spec_from_file_location(
            "vault_hook", os.path.join(_SCRIPTS_DIR, "vault_hook.py")
        )
        vh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vh)
        hook_domains = frozenset(vh.SENSITIVE_DOMAINS)
        assert hook_domains == expected, (
            f"vault_hook SENSITIVE_DOMAINS mismatch:\n"
            f"  extra in hook: {hook_domains - expected}\n"
            f"  missing from hook: {expected - hook_domains}"
        )

    def test_dashboard_view_matches_foundation(self):
        """dashboard_view._SENSITIVE_DOMAINS matches get_sensitive_domains()."""
        import scripts.dashboard_view as dv
        assert dv._SENSITIVE_DOMAINS == _foundation_domains()

    def test_diff_view_matches_foundation(self):
        """diff_view._SENSITIVE_DOMAINS matches get_sensitive_domains()."""
        import scripts.diff_view as dfv
        assert dfv._SENSITIVE_DOMAINS == _foundation_domains()

    def test_domain_view_matches_foundation(self):
        """domain_view._SENSITIVE_DOMAINS matches get_sensitive_domains()."""
        import scripts.domain_view as domv
        assert domv._SENSITIVE_DOMAINS == _foundation_domains()

    def test_memory_yaml_covers_all_foundation_domains(self):
        """DEBT-007: memory.yaml high_sensitivity_domains ⊇ SENSITIVE_FILES."""
        import yaml
        cfg_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "memory.yaml"
        )
        with open(cfg_path) as f:
            m = yaml.safe_load(f)
        memory_domains = set(m["privacy"]["high_sensitivity_domains"])
        foundation = _foundation_domains()
        missing = foundation - memory_domains
        assert not missing, (
            f"memory.yaml high_sensitivity_domains missing: {missing}. "
            "All vault-protected domains must be high-sensitivity in memory."
        )

    def test_domain_registry_requires_vault_matches_foundation(self):
        """RD-44: Every domain with requires_vault: true in domain_registry.yaml
        must be present in foundation.py SENSITIVE_FILES.

        This is the canonical cross-reference: domain_registry is the source of
        truth for 'this domain is sensitive'; foundation.py is the source of truth
        for 'these domains get encrypted at rest'. They must agree.
        """
        import yaml
        registry_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "domain_registry.yaml"
        )
        with open(registry_path, encoding="utf-8") as f:
            registry = yaml.safe_load(f) or {}

        domains_cfg = registry.get("domains") or {}
        vault_required_in_registry: set[str] = set()
        for domain_name, cfg in domains_cfg.items():
            if isinstance(cfg, dict) and cfg.get("requires_vault") is True:
                vault_required_in_registry.add(domain_name)

        foundation = _foundation_domains()

        missing_from_foundation = vault_required_in_registry - foundation
        assert not missing_from_foundation, (
            f"RD-44: Domains with requires_vault: true in domain_registry.yaml "
            f"but absent from foundation.py SENSITIVE_FILES: {sorted(missing_from_foundation)}. "
            f"Add them to SENSITIVE_FILES in scripts/foundation.py."
        )
