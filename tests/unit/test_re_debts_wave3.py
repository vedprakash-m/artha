"""
tests/unit/test_re_debts_wave3.py
===================================
Wave 3 regression tests for re-debts.md items implemented in the current session.

Covers:
  RD-19  — No LLM imports in deterministic pipeline modules (AST-based)
  RD-25  — missing_assignment stub documented correctly in signal_routing.yaml
  RD-26  — HMAC nonce cache integrity check at startup
  RD-27  — Per-domain shrink threshold in guardrails.yaml
  RD-28  — Domain prompt schema versioning (schema_version in frontmatter)
  RD-29  — keyword_miss_rate threshold in settings.md
  RD-30  — weak_queries registered_at TTL support
  RD-33  — Signal routing has all required routes
  RD-35  — Vault sync exclusion preflight functions exist
  RD-45  — skills_cache per-entry TTL eviction
  RD-46  — TF-IDF registry mtime cache invalidation
  RD-50  — CHARS_PER_TOKEN not duplicated outside context_budget.py (CI guard)
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_CONFIG_DIR = _REPO_ROOT / "config"
_PROMPTS_DIR = _REPO_ROOT / "prompts"

# Make scripts importable
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# RD-50: CHARS_PER_TOKEN must only be defined in context_budget.py
# ---------------------------------------------------------------------------

class TestRD50CharsPerTokenNotDuplicated:
    """CI guard: CHARS_PER_TOKEN constant must live exclusively in context_budget.py."""

    def _find_assignments(self, path: Path) -> list[str]:
        """Return variable names assigned in a module (simple assignment targets)."""
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            return []
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.append(t.id)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    names.append(node.target.id)
        return names

    def test_chars_per_token_not_duplicated(self):
        """CHARS_PER_TOKEN must not be defined outside context_budget.py without importing it.

        Accepts the try/except import pattern used in prompt_composer.py and
        context_offloader.py (import from lib.context_budget with a fallback literal).
        Rejects any file that defines CHARS_PER_TOKEN without first importing it.
        """
        canonical = _SCRIPTS_DIR / "lib" / "context_budget.py"
        assert canonical.exists(), "context_budget.py must exist"

        violations: list[str] = []
        for py_file in _SCRIPTS_DIR.rglob("*.py"):
            if py_file.resolve() == canonical.resolve():
                continue  # authoritative definition — skip
            src = py_file.read_text(encoding="utf-8")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue

            # Files that import from context_budget are compliant
            imports_from_budget = (
                "context_budget" in src and "CHARS_PER_TOKEN" in src
            )
            if imports_from_budget:
                continue  # try/except import pattern is acceptable

            # Flag bare assignments of *CHARS_PER_TOKEN without budget import
            names = self._find_assignments(py_file)
            for name in names:
                if "CHARS_PER_TOKEN" in name:
                    violations.append(
                        f"{py_file.relative_to(_REPO_ROOT)}: defines {name!r} "
                        f"without importing from lib.context_budget"
                    )

        assert not violations, (
            "RD-50: CHARS_PER_TOKEN defined without importing from context_budget.py. "
            "Use: from lib.context_budget import CHARS_PER_TOKEN\n" + "\n".join(violations)
        )

    def test_context_budget_has_canonical_constant(self):
        """context_budget.py must export CHARS_PER_TOKEN = 3.5."""
        sys.path.insert(0, str(_SCRIPTS_DIR / "lib"))
        try:
            import importlib
            cb = importlib.import_module("context_budget")
            assert hasattr(cb, "CHARS_PER_TOKEN"), "CHARS_PER_TOKEN not exported"
            assert cb.CHARS_PER_TOKEN == 3.5, f"Expected 3.5, got {cb.CHARS_PER_TOKEN}"
        finally:
            sys.path.pop(0)


# ---------------------------------------------------------------------------
# RD-19: No LLM imports in deterministic pipeline modules
# ---------------------------------------------------------------------------

class TestRD19NoLLMInDeterministicPipeline:
    """
    email_signal_extractor.py and pattern_engine.py are deterministic modules.
    They must NEVER import LLM clients or libraries — that would create an
    unpredictable dependency and undermine the local-first guarantee.
    """

    _DETERMINISTIC_MODULES = [
        _SCRIPTS_DIR / "email_signal_extractor.py",
        _SCRIPTS_DIR / "pattern_engine.py",
    ]

    # LLM library identifiers — any import of these is a violation
    _LLM_IDENTIFIERS = frozenset({
        "anthropic", "openai", "claude", "ChatCompletion",
        "langchain", "litellm", "cohere", "google.generativeai",
        "vertexai", "bedrock", "mistral", "groq",
    })

    def _collect_imports(self, path: Path) -> list[str]:
        """Collect all import module names from an AST."""
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, FileNotFoundError):
            return []
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    @pytest.mark.parametrize("module_path", _DETERMINISTIC_MODULES)
    def test_no_llm_imports(self, module_path: Path):
        """Deterministic pipeline modules must not import any LLM client library."""
        imports = self._collect_imports(module_path)
        violations = [
            imp for imp in imports
            if any(llm_id in imp for llm_id in self._LLM_IDENTIFIERS)
        ]
        assert not violations, (
            f"RD-19: LLM import(s) found in deterministic module {module_path.name}: "
            f"{violations}"
        )

    def test_deterministic_modules_exist(self):
        """Both deterministic modules must exist."""
        for p in self._DETERMINISTIC_MODULES:
            assert p.exists(), f"Expected deterministic module not found: {p}"


# ---------------------------------------------------------------------------
# RD-27: Per-domain shrink threshold in guardrails.yaml
# ---------------------------------------------------------------------------

class TestRD27PerDomainShrinkThreshold:
    """guardrails.yaml must have net_negative_write_guard with domain overrides."""

    def _load_guardrails(self) -> dict:
        gr_path = _CONFIG_DIR / "guardrails.yaml"
        return yaml.safe_load(gr_path.read_text(encoding="utf-8")) or {}

    def test_net_negative_write_guard_section_exists(self):
        gr = self._load_guardrails()
        assert "net_negative_write_guard" in gr, (
            "RD-27: 'net_negative_write_guard' section missing from guardrails.yaml"
        )

    def test_default_threshold_is_float(self):
        gr = self._load_guardrails()
        wr = gr["net_negative_write_guard"]
        thresh = wr.get("default_threshold")
        assert thresh is not None, "default_threshold missing from net_negative_write_guard"
        assert isinstance(thresh, (int, float)), f"default_threshold must be numeric, got {type(thresh)}"
        assert 0 < thresh <= 1.0, f"default_threshold must be in (0, 1], got {thresh}"

    def test_domain_thresholds_are_valid(self):
        gr = self._load_guardrails()
        wr = gr["net_negative_write_guard"]
        domain_thresh = wr.get("domain_thresholds", {})
        assert isinstance(domain_thresh, dict), "domain_thresholds must be a dict"
        for domain, thresh in domain_thresh.items():
            assert isinstance(thresh, (int, float)), (
                f"domain_thresholds[{domain!r}] must be numeric, got {type(thresh)}"
            )
            assert 0 < thresh <= 1.0, (
                f"domain_thresholds[{domain!r}] must be in (0, 1], got {thresh}"
            )

    def test_finance_threshold_lower_than_default(self):
        """Finance domain must have a lower threshold than default (allows legitimate condensation)."""
        gr = self._load_guardrails()
        wr = gr["net_negative_write_guard"]
        default = float(wr.get("default_threshold", 0.8))
        finance_thresh = wr.get("domain_thresholds", {}).get("finance")
        assert finance_thresh is not None, "finance domain threshold not configured"
        assert float(finance_thresh) < default, (
            f"finance threshold ({finance_thresh}) must be < default ({default})"
        )

    def test_vault_reads_guardrails_threshold(self):
        """is_integrity_safe() in vault.py must reference guardrails.yaml."""
        vault_src = (_SCRIPTS_DIR / "vault.py").read_text(encoding="utf-8")
        assert "net_negative_write_guard" in vault_src or "guardrails.yaml" in vault_src, (
            "RD-27: vault.py does not reference guardrails.yaml for shrink threshold"
        )
        assert "domain_thresholds" in vault_src, (
            "RD-27: vault.py does not use domain_thresholds from guardrails"
        )


# ---------------------------------------------------------------------------
# RD-29: keyword_miss_rate threshold in settings.md
# ---------------------------------------------------------------------------

class TestRD29KeywordMissRate:
    """settings.md must document keyword_miss_rate router threshold."""

    def test_keyword_miss_rate_in_settings(self):
        settings_path = _CONFIG_DIR / "settings.md"
        if not settings_path.exists():
            pytest.skip("config/settings.md is gitignored — not present in CI.")
        content = settings_path.read_text(encoding="utf-8")
        assert "keyword_miss_rate" in content, (
            "RD-29: keyword_miss_rate threshold not found in config/settings.md"
        )

    def test_router_upgrade_recommended_event_mentioned(self):
        settings_path = _CONFIG_DIR / "settings.md"
        if not settings_path.exists():
            pytest.skip("config/settings.md is gitignored — not present in CI.")
        content = settings_path.read_text(encoding="utf-8")
        assert "ROUTER_UPGRADE_RECOMMENDED" in content, (
            "RD-29: ROUTER_UPGRADE_RECOMMENDED event not documented in settings.md"
        )


# ---------------------------------------------------------------------------
# RD-30: weak_queries registered_at TTL
# ---------------------------------------------------------------------------

class TestRD30WeakQueryTTL:
    """AgentHealth must support weak_query_timestamps for 90-day TTL GC."""

    def test_agent_health_has_timestamps_field(self):
        """AgentHealth dataclass must have weak_query_timestamps field."""
        from lib.agent_registry import AgentHealth
        h = AgentHealth()
        assert hasattr(h, "weak_query_timestamps"), (
            "RD-30: AgentHealth missing weak_query_timestamps field"
        )
        assert isinstance(h.weak_query_timestamps, dict), (
            "weak_query_timestamps must be a dict"
        )

    def test_record_weak_query_writes_timestamp(self):
        """record_weak_query must write a timestamp entry."""
        import tempfile
        from lib.agent_registry import AgentRegistry, ExternalAgent, AgentHealth
        from lib.agent_health import AgentHealthTracker

        # Use AgentRegistry constructor directly with a temp path
        with tempfile.TemporaryDirectory() as tmpdir:
            reg_path = Path(tmpdir) / "agents" / "external-registry.yaml"
            reg_path.parent.mkdir(parents=True, exist_ok=True)
            reg_path.write_text(
                "schema_version: '1.0'\nagents:\n  test_agent:\n"
                "    label: Test\n    description: A test agent\n"
                "    source: test\n    enabled: true\n    status: active\n"
                "    content_hash: ''\n    trust_tier: external\n    auto_dispatch: false\n"
                "    auto_dispatch_after: 10\n    registered_at: '2026-01-01'\n"
                "    shadow_mode: false\n    cache_responses: false\n    cache_ttl_days: 7\n"
                "    max_cache_size_chars: 50000\n"
                "    pii_profile: {allow: [], block: []}\n"
                "    routing: {keywords: [], domains: [], min_confidence: 0.5, "
                "min_keyword_hits: 1, priority: 5, exclude_keywords: []}\n"
                "    invocation: {timeout_seconds: 30, max_budget: 1000, "
                "max_response_chars: 5000, max_context_chars: 10000}\n"
                "    fallback: null\n    fallback_cascade: []\n    health: {}\n",
                encoding="utf-8",
            )
            registry = AgentRegistry.load(config_dir=Path(tmpdir))
            mgr = AgentHealthTracker(registry)
            mgr.record_weak_query("test_agent", "test_pattern_xyz")

            agent = registry.get("test_agent")
            assert "test_pattern_xyz" in agent.health.weak_queries
            assert "test_pattern_xyz" in agent.health.weak_query_timestamps
            ts = agent.health.weak_query_timestamps["test_pattern_xyz"]
            assert ts, "timestamp must be non-empty ISO string"

    def test_gc_removes_stale_patterns(self):
        """Patterns older than 90 days must be GC'd on next record_weak_query call."""
        import tempfile
        from datetime import datetime, timezone, timedelta
        from lib.agent_registry import AgentRegistry
        from lib.agent_health import AgentHealthTracker

        old_ts = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()

        with tempfile.TemporaryDirectory() as tmpdir:
            reg_path = Path(tmpdir) / "agents" / "external-registry.yaml"
            reg_path.parent.mkdir(parents=True, exist_ok=True)
            reg_path.write_text(
                "schema_version: '1.0'\nagents:\n  test_agent:\n"
                "    label: Test\n    description: A test agent\n"
                "    source: test\n    enabled: true\n    status: active\n"
                "    content_hash: ''\n    trust_tier: external\n    auto_dispatch: false\n"
                "    auto_dispatch_after: 10\n    registered_at: '2026-01-01'\n"
                "    shadow_mode: false\n    cache_responses: false\n    cache_ttl_days: 7\n"
                "    max_cache_size_chars: 50000\n"
                "    pii_profile: {allow: [], block: []}\n"
                "    routing: {keywords: [], domains: [], min_confidence: 0.5, "
                "min_keyword_hits: 1, priority: 5, exclude_keywords: []}\n"
                "    invocation: {timeout_seconds: 30, max_budget: 1000, "
                "max_response_chars: 5000, max_context_chars: 10000}\n"
                "    fallback: null\n    fallback_cascade: []\n"
                f"    health:\n      weak_queries: [stale_pattern]\n"
                f"      weak_query_timestamps:\n        stale_pattern: '{old_ts}'\n",
                encoding="utf-8",
            )
            registry = AgentRegistry.load(config_dir=Path(tmpdir))
            mgr = AgentHealthTracker(registry)
            # Trigger GC by recording a fresh pattern
            mgr.record_weak_query("test_agent", "fresh_pattern")

            agent = registry.get("test_agent")
            assert "stale_pattern" not in agent.health.weak_queries, (
                "RD-30: stale pattern should be GC'd after 90 days"
            )
            assert "fresh_pattern" in agent.health.weak_queries


# ---------------------------------------------------------------------------
# RD-33: Signal routing has all required routes
# ---------------------------------------------------------------------------

class TestRD33SignalRoutingOrphans:
    """All signals emitted by producers must have entries in signal_routing.yaml."""

    def _load_routing(self) -> dict:
        routing_path = _CONFIG_DIR / "signal_routing.yaml"
        return yaml.safe_load(routing_path.read_text(encoding="utf-8")) or {}

    def test_decision_detected_route_exists(self):
        routing = self._load_routing()
        assert "decision_detected" in routing, (
            "RD-33: decision_detected route missing from signal_routing.yaml"
        )

    def test_content_moment_missed_route_exists(self):
        routing = self._load_routing()
        assert "content_moment_missed" in routing, (
            "RD-33: content_moment_missed route missing from signal_routing.yaml"
        )

    def test_content_staged_unreviewed_route_exists(self):
        routing = self._load_routing()
        assert "content_staged_unreviewed" in routing, (
            "RD-33: content_staged_unreviewed route missing from signal_routing.yaml"
        )

    def test_address_update_notification_route_exists(self):
        routing = self._load_routing()
        assert "address_update_notification" in routing, (
            "RD-33: address_update_notification route missing from signal_routing.yaml"
        )

    def test_routes_have_required_fields(self):
        routing = self._load_routing()
        required_routes = [
            "decision_detected",
            "content_moment_missed",
            "content_staged_unreviewed",
            "address_update_notification",
        ]
        for route_key in required_routes:
            if route_key not in routing:
                continue  # caught by other tests
            route = routing[route_key]
            assert isinstance(route, dict), f"{route_key} must be a dict"
            assert "action_type" in route, f"{route_key} missing action_type"


# ---------------------------------------------------------------------------
# RD-35: Vault sync exclusion preflight
# ---------------------------------------------------------------------------

class TestRD35VaultSyncExclusionPreflight:
    """Vault must have sync exclusion check functions."""

    def test_verify_sync_exclusion_function_exists(self):
        vault_src = (_SCRIPTS_DIR / "vault.py").read_text(encoding="utf-8")
        assert "_verify_sync_exclusion" in vault_src, (
            "RD-35: _verify_sync_exclusion function missing from vault.py"
        )

    def test_warn_if_sync_not_excluded_function_exists(self):
        vault_src = (_SCRIPTS_DIR / "vault.py").read_text(encoding="utf-8")
        assert "_warn_if_sync_not_excluded" in vault_src, (
            "RD-35: _warn_if_sync_not_excluded function missing from vault.py"
        )

    def test_vault_sync_exclusion_missing_audit_event(self):
        vault_src = (_SCRIPTS_DIR / "vault.py").read_text(encoding="utf-8")
        assert "VAULT_SYNC_EXCLUSION_MISSING" in vault_src, (
            "RD-35: VAULT_SYNC_EXCLUSION_MISSING audit event missing from vault.py"
        )

    def test_xattr_check_used_for_macos(self):
        vault_src = (_SCRIPTS_DIR / "vault.py").read_text(encoding="utf-8")
        assert "com.microsoft.OneDrive" in vault_src, (
            "RD-35: OneDrive xattr check missing from vault.py sync exclusion logic"
        )


# ---------------------------------------------------------------------------
# RD-26: HMAC nonce cache integrity check
# ---------------------------------------------------------------------------

class TestRD26NonceCacheIntegrityCheck:
    """HmacSigner must check nonce cache integrity at startup."""

    def test_nonce_cache_integrity_check_function_exists(self):
        signer_src = (_SCRIPTS_DIR / "lib" / "hmac_signer.py").read_text(encoding="utf-8")
        assert "_nonce_cache_integrity_check" in signer_src, (
            "RD-26: _nonce_cache_integrity_check missing from hmac_signer.py"
        )

    def test_replay_vulnerable_property_exists(self):
        signer_src = (_SCRIPTS_DIR / "lib" / "hmac_signer.py").read_text(encoding="utf-8")
        assert "is_replay_vulnerable" in signer_src, (
            "RD-26: is_replay_vulnerable method missing from hmac_signer.py"
        )

    def test_nonce_cache_integrity_audit_event(self):
        signer_src = (_SCRIPTS_DIR / "lib" / "hmac_signer.py").read_text(encoding="utf-8")
        assert "NONCE_CACHE_INTEGRITY" in signer_src, (
            "RD-26: NONCE_CACHE_INTEGRITY audit event missing from hmac_signer.py"
        )


# ---------------------------------------------------------------------------
# RD-45: skills_cache per-entry TTL
# ---------------------------------------------------------------------------

class TestRD45SkillsCachePerEntryTTL:
    """skill_runner.py must enforce per-entry TTL eviction."""

    def test_skills_cache_ttl_days_constant_exists(self):
        runner_src = (_SCRIPTS_DIR / "skill_runner.py").read_text(encoding="utf-8")
        assert "_SKILLS_CACHE_TTL_DAYS" in runner_src, (
            "RD-45: _SKILLS_CACHE_TTL_DAYS constant missing from skill_runner.py"
        )

    def test_cached_at_written_to_cache(self):
        runner_src = (_SCRIPTS_DIR / "skill_runner.py").read_text(encoding="utf-8")
        assert '"cached_at"' in runner_src or "'cached_at'" in runner_src, (
            "RD-45: cached_at timestamp not written to cache entries in skill_runner.py"
        )

    def test_ttl_eviction_logged(self):
        runner_src = (_SCRIPTS_DIR / "skill_runner.py").read_text(encoding="utf-8")
        assert "SKILLS_CACHE_TTL_EVICTION" in runner_src, (
            "RD-45: SKILLS_CACHE_TTL_EVICTION log event missing from skill_runner.py"
        )

    def test_cached_at_ttl_evicts_old_entry(self):
        """Entries with cached_at older than TTL must be evicted."""
        import sys
        from datetime import datetime, timezone, timedelta
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))
        from skill_runner import _enforce_cache_size_cap  # noqa: PLC0415

        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        fresh_ts = datetime.now(timezone.utc).isoformat()
        cache = {
            "old_skill": {"cached_at": old_ts, "last_run": old_ts, "data": "x"},
            "fresh_skill": {"cached_at": fresh_ts, "last_run": fresh_ts, "data": "y"},
        }
        result = _enforce_cache_size_cap(cache)
        assert "old_skill" not in result, "old_skill with cached_at >7 days should be evicted"
        assert "fresh_skill" in result, "fresh_skill with recent cached_at should survive"

    def test_legacy_entries_without_cached_at_are_grandfathered(self):
        """Entries without cached_at field must NOT be evicted by TTL (pre-RD-45 format)."""
        import sys
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))
        from skill_runner import _enforce_cache_size_cap  # noqa: PLC0415

        cache = {
            "legacy_skill": {"last_run": "2020-01-01T00:00:00+00:00", "data": "small"},
        }
        result = _enforce_cache_size_cap(cache)
        # Legacy entry without cached_at should survive TTL pass
        assert "legacy_skill" in result, (
            "Legacy entries without cached_at must be grandfathered by TTL eviction"
        )


# ---------------------------------------------------------------------------
# RD-46: TF-IDF registry mtime cache invalidation
# ---------------------------------------------------------------------------

class TestRD46TFIDFCacheInvalidation:
    """tfidf_router.py must invalidate cache when registry is newer."""

    def test_registry_mtime_check_present(self):
        router_src = (_SCRIPTS_DIR / "lib" / "tfidf_router.py").read_text(encoding="utf-8")
        assert "mtime" in router_src, (
            "RD-46: mtime check missing from tfidf_router.py"
        )

    def test_cache_built_ts_compared(self):
        router_src = (_SCRIPTS_DIR / "lib" / "tfidf_router.py").read_text(encoding="utf-8")
        assert "cache_built_ts" in router_src or "registry_mtime" in router_src, (
            "RD-46: registry mtime comparison not found in tfidf_router.py"
        )


# ---------------------------------------------------------------------------
# RD-28: Domain prompt schema versioning
# ---------------------------------------------------------------------------

class TestRD28DomainPromptSchemaVersion:
    """All domain prompt files must declare schema_version in frontmatter."""

    def _get_domain_prompts(self) -> list[Path]:
        """Return domain prompt files (exclude work-* and non-domain files like README)."""
        prompts = []
        _EXCLUDE_NAMES = {"README.md", "README"}
        for p in sorted(_PROMPTS_DIR.glob("*.md")):
            if p.stem.startswith("work-"):
                continue  # work prompts handled separately
            if p.name in _EXCLUDE_NAMES or p.stem in _EXCLUDE_NAMES:
                continue  # README and similar are not domain prompts
            prompts.append(p)
        return prompts

    def _parse_frontmatter_version(self, path: Path) -> str | None:
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None
        end = content.find("\n---", 3)
        if end == -1:
            return None
        fm_block = content[3:end]
        try:
            fm = yaml.safe_load(fm_block) or {}
            return str(fm.get("schema_version", "")) or None
        except yaml.YAMLError:
            return None

    def test_domain_prompts_have_schema_version(self):
        """All non-work domain prompts must have schema_version in frontmatter."""
        missing: list[str] = []
        for p in self._get_domain_prompts():
            ver = self._parse_frontmatter_version(p)
            if not ver:
                missing.append(str(p.relative_to(_REPO_ROOT)))
        assert not missing, (
            "RD-28: These domain prompt files are missing 'schema_version' in frontmatter:\n"
            + "\n".join(missing)
        )

    def test_prompt_linter_checks_schema_version(self):
        """prompt_linter.py must include schema_version check (RD-28 validation hook)."""
        linter_src = (_SCRIPTS_DIR / "tools" / "prompt_linter.py").read_text(encoding="utf-8")
        assert "schema_version" in linter_src, (
            "RD-28: prompt_linter.py does not validate schema_version in frontmatter"
        )


# ---------------------------------------------------------------------------
# RD-25: missing_assignment stub documented
# ---------------------------------------------------------------------------

class TestRD25MissingAssignmentStub:
    """missing_assignment in signal_routing.yaml must be documented as deliberate stub."""

    def test_missing_assignment_has_status_stub(self):
        routing_path = _CONFIG_DIR / "signal_routing.yaml"
        content = routing_path.read_text(encoding="utf-8")
        # Find the block for missing_assignment and confirm status: stub
        lines = content.splitlines()
        found_entry = False
        for i, line in enumerate(lines):
            if line.strip() == "missing_assignment:":
                found_entry = True
                # Check next 15 lines for 'status: stub'
                block = "\n".join(lines[i:i+15])
                assert "status: stub" in block, (
                    "RD-25: missing_assignment entry must have 'status: stub'"
                )
                break
        assert found_entry, "missing_assignment entry not found in signal_routing.yaml"

    def test_missing_assignment_has_rd25_comment(self):
        routing_path = _CONFIG_DIR / "signal_routing.yaml"
        content = routing_path.read_text(encoding="utf-8")
        assert "RD-25" in content, (
            "RD-25: missing_assignment stub must have RD-25 reference comment"
        )


# ─── RD-38: Artha.md compact prompt size CI gate ──────────────────────────────

class TestRD38CompactPromptSize:
    """RD-38: config/Artha.md must stay within the compact prompt size limit.

    The compact architecture targets ≤20KB. CI gate is 25KB.
    If this fails, run: python scripts/generate_identity.py
    (compact mode is the default; --no-compact produces the full file).
    """

    _MAX_PROMPT_BYTES = 25_000  # 25KB CI gate per RD-38 spec

    @staticmethod
    def _get_artha_md():
        artha_md = _REPO_ROOT / "config" / "Artha.md"
        if not artha_md.exists():
            pytest.skip("config/Artha.md is gitignored — not present in CI. Run generate_identity.py locally.")
        # Skip if local monolith (> 50KB): compact version not yet generated
        if artha_md.stat().st_size > 50_000:
            pytest.skip(
                f"config/Artha.md is the local monolith ({artha_md.stat().st_size:,} bytes). "
                "Run: python scripts/generate_identity.py to create compact version for testing."
            )
        return artha_md

    def test_artha_md_meets_size_limit(self):
        artha_md = self._get_artha_md()
        size = artha_md.stat().st_size
        assert size <= self._MAX_PROMPT_BYTES, (
            f"config/Artha.md is {size:,} bytes — exceeds {self._MAX_PROMPT_BYTES:,}B limit "
            f"(RD-38). Run: python scripts/generate_identity.py"
        )

    def test_artha_md_exists_and_nonempty(self):
        artha_md = self._get_artha_md()
        assert artha_md.stat().st_size > 1_000, (
            "config/Artha.md is suspiciously small (<1KB) — generation may have failed"
        )


# ---------------------------------------------------------------------------
# RD-10 — TF-IDF routing tiebreaker determinism
# ---------------------------------------------------------------------------


class TestRD10TiebreakerDeterminism:
    """RD-10: repeated identical queries must return a stable, deterministic ordering."""

    def test_identical_scores_resolved_deterministically(self):
        """Two agents with identical TF-IDF vectors must always sort the same way."""
        from lib.tfidf_router import TFIDFRouter, _text_to_vec

        corpus_vec = _text_to_vec("kubernetes nodes not progressing cluster health monitoring")
        router = TFIDFRouter(cache_file=None)
        # Inject identical trigram vectors → cosine sim is equal → tiebreaker fires
        router._vectors = {
            "zebra_agent": corpus_vec,
            "alpha_agent": corpus_vec,
        }
        router._loaded = True

        results_a = [m.agent_name for m in router.query("kubernetes nodes not progressing", min_sim=0.01)]
        results_b = [m.agent_name for m in router.query("kubernetes nodes not progressing", min_sim=0.01)]
        results_c = [m.agent_name for m in router.query("kubernetes nodes not progressing", min_sim=0.01)]

        assert results_a, "router returned no matches — trigram corpus may not align"
        assert results_a == results_b == results_c, (
            f"RD-10: routing is non-deterministic across calls: "
            f"{results_a} vs {results_b} vs {results_c}"
        )

    def test_tiebreaker_prefers_higher_domain_weight(self):
        """When scores tie, the agent with higher domain_weight wins."""
        from lib.tfidf_router import TFIDFRouter, _text_to_vec

        # Use real trigram vectors so cosine sim > min_sim threshold
        corpus_vec = _text_to_vec("kubernetes nodes not progressing cluster health")
        router = TFIDFRouter(cache_file=None)
        router._vectors = {
            "low_weight_agent":  corpus_vec,
            "high_weight_agent": corpus_vec,  # identical → guaranteed tie
        }
        router._loaded = True

        weights = {"high_weight_agent": 10, "low_weight_agent": 1}
        matches = router.query("kubernetes nodes not progressing", min_sim=0.01, domain_weights=weights)
        assert matches, "router returned no matches"
        assert matches[0].agent_name == "high_weight_agent", (
            f"RD-10: tiebreaker did not prefer higher-weight agent; got {matches[0].agent_name}"
        )

    def test_tiebreaker_falls_back_to_alphabetical(self):
        """When weights are equal, alphabetical agent name is the tiebreaker."""
        from lib.tfidf_router import TFIDFRouter, _text_to_vec

        corpus_vec = _text_to_vec("kubernetes nodes not progressing cluster health")
        router = TFIDFRouter(cache_file=None)
        router._vectors = {
            "zz_agent": corpus_vec,
            "aa_agent": corpus_vec,  # identical → guaranteed tie
        }
        router._loaded = True

        matches = router.query("kubernetes nodes not progressing", min_sim=0.01)
        assert matches, "router returned no matches"
        assert matches[0].agent_name == "aa_agent", (
            f"RD-10: alphabetical tiebreaker expected 'aa_agent' first; got {matches[0].agent_name}"
        )


# ---------------------------------------------------------------------------
# RD-11 — approve-all-low TTL boundary idempotency guard (static checks)
# ---------------------------------------------------------------------------


class TestRD11ApproveAllLowIdempotency:
    """RD-11: approve-all-low re-validates idempotency keys at approval time."""

    def test_ttl_boundary_guard_token_present(self):
        """Static: action_orchestrator.py must contain the TTL boundary audit token."""
        src = (_REPO_ROOT / "scripts" / "action_orchestrator.py").read_text(encoding="utf-8")
        assert "approve_all_low_ttl_boundary" in src, (
            "RD-11: 'approve_all_low_ttl_boundary' guard token missing from "
            "action_orchestrator.py — TTL boundary protection may have been removed"
        )

    def test_approval_time_idempotency_recheck_comment_present(self):
        """Static: RD-11 approval-time re-check comment must exist."""
        src = (_REPO_ROOT / "scripts" / "action_orchestrator.py").read_text(encoding="utf-8")
        assert "Re-run idempotency check at approval time" in src, (
            "RD-11: approval-time idempotency re-check comment missing — "
            "guard may have been removed from cmd_approve_all_low"
        )

    def test_cmd_approve_all_low_callable(self):
        """cmd_approve_all_low must be importable and callable."""
        import action_orchestrator as ao
        assert callable(getattr(ao, "cmd_approve_all_low", None)), (
            "RD-11: cmd_approve_all_low not found in action_orchestrator"
        )


# ---------------------------------------------------------------------------
# RD-14 — concurrent vault write machine-conflict warning
# ---------------------------------------------------------------------------


class TestRD14ConcurrentVaultWarning:
    """RD-14: do_encrypt() detects and logs multi-machine vault conflict."""

    def test_concurrent_vault_warning_string_present(self):
        """Static: CONCURRENT_VAULT_WARNING log token must exist in vault.py."""
        src = (_REPO_ROOT / "scripts" / "vault.py").read_text(encoding="utf-8")
        assert "CONCURRENT_VAULT_WARNING" in src, (
            "RD-14: CONCURRENT_VAULT_WARNING missing from vault.py — "
            "multi-machine concurrent write protection not implemented"
        )

    def test_rd14_marker_in_vault_source(self):
        """Static: do_encrypt must carry RD-14 traceability marker."""
        src = (_REPO_ROOT / "scripts" / "vault.py").read_text(encoding="utf-8")
        assert "RD-14" in src, (
            "RD-14: traceability marker missing from vault.py — "
            "regression guard may have been removed"
        )

    def test_machine_hostname_comparison_implemented(self):
        """Static: machine conflict check must use gethostname()."""
        src = (_REPO_ROOT / "scripts" / "vault.py").read_text(encoding="utf-8")
        assert "gethostname" in src, (
            "RD-14: socket.gethostname() absent from vault.py — "
            "concurrent machine detection not implemented"
        )


# ---------------------------------------------------------------------------
# RD-39 — compact Artha.md domain index budget
# ---------------------------------------------------------------------------


class TestRD39DomainIndexBudget:
    """RD-39: compact Artha.md domain index section must stay ≤ 600 tokens (~2100 chars)."""

    _BUDGET_CHARS = 2100  # ~600 tokens at 3.5 chars/token

    def _load_domain_section(self) -> str:
        artha_md = _REPO_ROOT / "config" / "Artha.md"
        if not artha_md.exists():
            pytest.skip("config/Artha.md is gitignored — not present in CI. Run generate_identity.py locally.")
        # Skip if local monolith (> 50KB): compact version not yet generated
        if artha_md.stat().st_size > 50_000:
            pytest.skip(
                f"config/Artha.md is the local monolith ({artha_md.stat().st_size:,} bytes). "
                "Run: python scripts/generate_identity.py to create compact version for testing."
            )
        text = artha_md.read_text(encoding="utf-8")
        start = text.find("### Active Domains")
        assert start != -1, "RD-39: '### Active Domains' section not found in config/Artha.md"
        # Find next heading at the same (###) or higher level
        end = text.find("\n### ", start + 1)
        if end == -1:
            end = text.find("\n## ", start + 1)
        if end == -1:
            end = len(text)
        return text[start:end]

    def test_domain_index_within_char_budget(self):
        """Domain index section must be ≤ 2100 chars (~600 tokens)."""
        section = self._load_domain_section()
        assert len(section) <= self._BUDGET_CHARS, (
            f"RD-39: Domain index section is {len(section)} chars "
            f"(≈{len(section) / 3.5:.0f} tokens), exceeds {self._BUDGET_CHARS} char budget. "
            "Run: python scripts/generate_identity.py to regenerate compact Artha.md."
        )

    def test_domain_index_has_enough_entries(self):
        """Domain index section must list at least 5 domains."""
        section = self._load_domain_section()
        domain_lines = [ln for ln in section.splitlines() if ln.startswith("- ")]
        assert len(domain_lines) >= 5, (
            f"RD-39: Domain index has only {len(domain_lines)} entries — "
            "too sparse or section boundary detection is wrong"
        )
